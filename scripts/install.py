"""Linux installer: isolated Python, official source ingestion, systemd user autostart."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
SERVICE = "odoo19-docs.service"
MANAGED_FILES = (
    "src",
    "scripts",
    "docs",
    "examples",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "README.md",
    "LICENSE",
    "NOTICE",
    "install.sh",
    "uninstall.sh",
    "odoo19ctl",
)


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+ " + " ".join(args), flush=True)
    return subprocess.run(args, check=True, **kwargs)


def unit_quote(value: str, *, command: bool = False) -> str:
    if any(c in value for c in "\r\n\x00"):
        raise ValueError("Newlines and NUL are not allowed in installation paths")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def service_unit(prefix: Path, data: Path, port: int) -> str:
    # WorkingDirectory consumes a single literal path; unlike ExecStart it does not unquote it.
    directory = str(prefix)
    unit_quote(directory)  # Validate control characters before writing a unit directive.
    executable = prefix / ".venv/bin/python"
    args = [
        str(executable),
        "-m",
        "src.server",
        "--transport",
        "streamable-http",
        "--port",
        str(port),
    ]
    return "\n".join(
        [
            "[Unit]",
            "Description=Odoo 19 Documentation MCP (local RAG)",
            "StartLimitIntervalSec=60",
            "StartLimitBurst=5",
            "",
            "[Service]",
            "Type=exec",
            "WorkingDirectory=" + directory.replace("%", "%%"),
            "ExecStart=" + " ".join(unit_quote(arg, command=True) for arg in args),
            "Environment=" + unit_quote("ODOO_RAG_DATA_DIR=" + str(data)),
            "Environment=ODOO_RAG_EMBEDDING_PROVIDER=local",
            "Environment=ODOO_RAG_OFFLINE=true",
            "Environment=PYTHONUNBUFFERED=1",
            "Restart=on-failure",
            "RestartSec=3",
            "TimeoutStartSec=120",
            "TimeoutStopSec=30",
            "NoNewPrivileges=true",
            "UMask=0077",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def locate_uv(prefix: Path, env: dict[str, str]) -> str:
    found = shutil.which("uv")
    if found:
        return found
    # Download the vendor installer to disk first; never pipe an HTTP response to a shell.
    print("Descargando uv desde el sitio oficial de Astral...", flush=True)
    tools = prefix / ".tools"
    tools.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen("https://astral.sh/uv/install.sh", timeout=60) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise RuntimeError("Unexpected uv installer size")
    with tempfile.TemporaryDirectory(prefix="odoo19-uv-") as directory:
        script = Path(directory) / "uv-install.sh"
        script.write_bytes(payload)
        vendor_env = {**env, "UV_INSTALL_DIR": str(tools), "UV_NO_MODIFY_PATH": "1"}
        run(["sh", str(script)], env=vendor_env)
    return str(tools / "uv")


def write_client_configs(prefix: Path, data: Path, port: int) -> None:
    config = prefix / "client-config"
    config.mkdir(exist_ok=True)
    url = f"http://127.0.0.1:{port}/mcp"
    (config / "mcp-http.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "odoo19-docs": {
                        "url": url,
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (config / "mcp-stdio.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "odoo19-docs": {
                        "command": str(prefix / ".venv/bin/python"),
                        "args": [str(prefix / "src/server.py")],
                        "cwd": str(prefix),
                        "env": {"ODOO_RAG_DATA_DIR": str(data), "ODOO_RAG_OFFLINE": "true"},
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # JSON string escapes are valid TOML basic-string escapes for these local paths.
    (config / "codex-http.toml").write_text(
        "[mcp_servers.odoo19-docs]\nurl = " + json.dumps(url) + "\n", encoding="utf-8"
    )


def wait_ready(port: int, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/readyz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                payload = json.load(response)
                if payload.get("ready"):
                    return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError(
        f"El servicio no quedó listo. Consulte: journalctl --user -u {SERVICE} -n 50"
    )


def install(args: argparse.Namespace) -> dict:
    if sys.platform != "linux":
        raise RuntimeError("Este instalador automático requiere Linux con systemd")
    if os.geteuid() == 0:
        raise RuntimeError("Ejecute como usuario normal, sin sudo")
    if not shutil.which("git"):
        raise RuntimeError("Se necesita Git: instálelo con el gestor de paquetes de su sistema")
    if not (SOURCE / "uv.lock").is_file():
        raise RuntimeError("Descargue o clone el proyecto completo antes de ejecutar install.sh")
    prefix = args.prefix.expanduser().resolve()
    data = (
        args.use_existing_data.expanduser().resolve() if args.use_existing_data else prefix / "data"
    )
    if prefix == SOURCE or SOURCE.is_relative_to(prefix):
        raise ValueError("El destino debe ser independiente del árbol fuente")
    marker = prefix / ".odoo19-install.json"
    if prefix.exists() and any(prefix.iterdir()) and not marker.is_file():
        raise ValueError("El destino no está vacío ni pertenece a una instalación administrada")
    if args.use_existing_data and not (data / "chroma_db/manifest.json").is_file():
        raise ValueError("--use-existing-data requiere una carpeta data con índice ya construido")
    if not args.no_service:
        if not shutil.which("systemctl"):
            raise RuntimeError(
                "Se necesita systemd; use --no-service para instalar solo el entorno"
            )
        run(["systemctl", "--user", "show-environment"], stdout=subprocess.DEVNULL)
        # Refuse collisions before modifying an existing installation.
        active = (
            subprocess.run(["systemctl", "--user", "is-active", "--quiet", SERVICE]).returncode == 0
        )
        if not active:
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", args.port))
                except OSError as exc:
                    raise RuntimeError(f"El puerto {args.port} ya está ocupado") from exc
        else:
            if not marker.is_file():
                raise RuntimeError("Ya hay otro servicio Odoo19 instalado; use su mismo --prefix")
            run(["systemctl", "--user", "stop", SERVICE])
    prefix.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"source": str(SOURCE), "state": "installing"}), encoding="utf-8")
    for name in MANAGED_FILES:
        source, target = SOURCE / name, prefix / name
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        elif source.is_file():
            shutil.copy2(source, target)
    env = {
        **os.environ,
        "UV_PYTHON_INSTALL_DIR": str(prefix / ".python"),
        "UV_CACHE_DIR": os.getenv("UV_CACHE_DIR", str(prefix / ".cache/uv")),
        "ODOO_RAG_DATA_DIR": str(data),
        "ODOO_RAG_EMBEDDING_PROVIDER": "local",
        "ODOO_RAG_OFFLINE": "true",
    }
    # Source-dependent settings must not silently change a local-model installation.
    for key in (
        "ODOO_RAG_LOCAL_MODEL",
        "ODOO_RAG_LOCAL_REVISION",
        "ODOO_RAG_CHUNK_SIZE",
        "ODOO_RAG_CHUNK_OVERLAP",
    ):
        env.pop(key, None)
    uv = locate_uv(prefix, env)
    run([uv, "sync", "--locked", "--no-dev", "--python", "3.12"], cwd=prefix, env=env)
    python = str(prefix / ".venv/bin/python")
    if args.use_existing_data:
        run([python, "-m", "src.status"], cwd=prefix, env=env)
    else:
        run([python, "-m", "src.ingest"], cwd=prefix, env=env)
    run([python, "-m", "src.status"], cwd=prefix, env=env)
    write_client_configs(prefix, data, args.port)
    unit_file = (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "systemd/user"
        / SERVICE
    )
    if not args.no_service:
        unit_file.parent.mkdir(parents=True, exist_ok=True)
        unit_file.write_text(service_unit(prefix, data, args.port), encoding="utf-8")
        if shutil.which("systemd-analyze"):
            run(["systemd-analyze", "--user", "verify", str(unit_file)])
        run(["systemctl", "--user", "daemon-reload"])
        if not args.no_linger:
            run(["loginctl", "enable-linger", getpass.getuser()])
            linger = subprocess.run(
                ["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if linger != "yes":
                raise RuntimeError(
                    "No se confirmó Linger=yes; use --no-linger para arranque al login"
                )
        run(["systemctl", "--user", "enable", "--now", SERVICE])
        wait_ready(args.port)
        run(
            [python, "-m", "src.probe", "--url", f"http://127.0.0.1:{args.port}/mcp"],
            cwd=prefix,
            env=env,
        )
    record = {
        "state": "installed",
        "prefix": str(prefix),
        "data": str(data),
        "port": args.port,
        "service": None if args.no_service else SERVICE,
        "autostart": "none" if args.no_service else ("login" if args.no_linger else "boot"),
        "unit_file": str(unit_file),
        "url": f"http://127.0.0.1:{args.port}/mcp",
    }
    marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        type=Path,
        default=Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        / "odoo19-mcp-rag",
    )
    parser.add_argument("--port", type=int, default=8019)
    parser.add_argument(
        "--use-existing-data",
        type=Path,
        help="Reuse an existing data/ without downloading/indexing",
    )
    parser.add_argument("--no-service", action="store_true", help="Prepare files/environment only")
    parser.add_argument(
        "--no-linger", action="store_true", help="Autostart at user login instead of boot"
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged port between 1024 and 65535")
    try:
        print(json.dumps(install(args), indent=2, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Instalación incompleta: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
