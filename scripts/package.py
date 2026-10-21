"""Create a source-only, relocatable release; fail before packaging credentials/local paths."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOTS = (
    "src",
    "scripts",
    "tests",
    "docs",
    "examples",
    ".github",
    "data",
    "reports/.gitkeep",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".env.example",
    ".gitignore",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "install.sh",
    "uninstall.sh",
    "odoo19ctl",
    "mcp_config_example.json",
)
SECRET = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    rb"github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def public_files(root: Path = ROOT) -> list[Path]:
    files = set()
    for entry in PUBLIC_ROOTS:
        candidate = root / entry
        if candidate.is_symlink():
            raise ValueError(f"Symlink not allowed in public source: {entry}")
        paths = candidate.rglob("*") if candidate.is_dir() else [candidate]
        for path in paths:
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if relative.parts[0] == "data" and path.name != ".gitkeep":
                continue
            if path.is_symlink():
                raise ValueError(f"Symlink not allowed in public source: {relative}")
            if not path.is_file():
                continue
            payload = path.read_bytes()
            if path.name == ".env" or SECRET.search(payload):
                raise ValueError(f"Potential credential in public source: {relative}")
            if str(Path.home()).encode() in payload:
                raise ValueError(f"Machine-specific home path in public source: {relative}")
            files.add(path)
    return sorted(files)


def build(destination: Path) -> Path:
    files = public_files()
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    folder = metadata["name"] + "-" + metadata["version"]
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / (folder + ".tar.gz")
    with (
        archive.open("wb") as stream,
        gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as compressed,
    ):
        with tarfile.open(fileobj=compressed, mode="w") as tar:
            for path in files:
                info = tar.gettarinfo(
                    str(path), arcname=folder + "/" + path.relative_to(ROOT).as_posix()
                )
                info.uid, info.gid, info.uname, info.gname, info.mtime = 0, 0, "", "", 0
                info.mode = (
                    0o755 if path.name in {"install.sh", "uninstall.sh", "odoo19ctl"} else 0o644
                )
                with path.open("rb") as source:
                    tar.addfile(info, source)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / "SHA256SUMS").write_text(checksum + "  " + archive.name + "\n", encoding="utf-8")
    print(f"{len(files)} source files, {archive.stat().st_size} bytes, SHA256 {checksum}")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.output))


if __name__ == "__main__":
    main()
