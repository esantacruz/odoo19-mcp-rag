"""Control an installed service without deleting user data or changing client settings."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SERVICE = "odoo19-docs.service"


def main() -> None:
    default = Path(__file__).resolve().parents[1]
    if not (default / ".odoo19-install.json").is_file():
        default = (
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
            / "odoo19-mcp-rag"
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["status", "start", "stop", "restart", "logs", "check", "update", "uninstall"],
    )
    parser.add_argument("--prefix", type=Path, default=default)
    args = parser.parse_args()
    prefix = args.prefix.expanduser().resolve()
    marker = prefix / ".odoo19-install.json"
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        if record.get("state") != "installed":
            raise RuntimeError("La instalación no está completa o ya está desinstalada")
        data = Path(record["data"])
        env = {
            **os.environ,
            "ODOO_RAG_DATA_DIR": str(data),
            "ODOO_RAG_EMBEDDING_PROVIDER": "local",
            "ODOO_RAG_OFFLINE": "true",
        }
        python = str(prefix / ".venv/bin/python")
        if args.action == "check":
            subprocess.run([python, "-m", "src.status"], cwd=prefix, env=env, check=True)
            subprocess.run(
                [python, "-m", "src.probe", "--url", record["url"]], cwd=prefix, env=env, check=True
            )
        elif args.action == "logs":
            subprocess.run(
                ["journalctl", "--user", "-u", SERVICE, "-n", "100", "--no-pager"], check=True
            )
        elif args.action == "update":
            was_active = (
                subprocess.run(["systemctl", "--user", "is-active", "--quiet", SERVICE]).returncode
                == 0
            )
            if was_active:
                subprocess.run(["systemctl", "--user", "stop", SERVICE], check=True)
            # An ingestion error deliberately leaves the service stopped until repaired.
            subprocess.run([python, "-m", "src.ingest"], cwd=prefix, env=env, check=True)
            if was_active:
                subprocess.run(["systemctl", "--user", "start", SERVICE], check=True)
        elif args.action == "uninstall":
            unit = Path(record["unit_file"])
            expected = (
                Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
                / "systemd/user"
                / SERVICE
            )
            if unit != expected:
                raise ValueError("Unexpected service path in installation marker")
            subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE], check=True)
            unit.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            record["state"] = "uninstalled"
            marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            print(f"Servicio retirado. Se conservaron datos y archivos en {prefix} y {data}.")
            print("Linger se conserva porque puede ser utilizado por otros servicios del usuario.")
        else:
            command = ["systemctl", "--user", args.action, SERVICE]
            if args.action == "status":
                command.append("--no-pager")
            subprocess.run(command, check=True)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
