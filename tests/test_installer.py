from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import install as installer


def options(prefix: Path, **overrides) -> argparse.Namespace:
    return argparse.Namespace(
        **{
            "prefix": prefix,
            "port": 8019,
            "no_service": True,
            "no_linger": False,
            "use_existing_data": None,
            **overrides,
        }
    )


def test_install_generates_relocatable_configs_and_omits_source_secrets(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name, content in {"uv.lock": "lock", "pyproject.toml": "project", ".env": "secret"}.items():
        (source / name).write_text(content)
    (source / "src").mkdir()
    (source / "src/server.py").write_text("# server")
    (source / "data").mkdir()
    (source / "data/private.txt").write_text("private download")
    monkeypatch.setattr(installer, "SOURCE", source)
    monkeypatch.setattr(installer, "locate_uv", lambda *args: "/fake/uv")
    monkeypatch.setattr(installer.shutil, "which", lambda _: "/fake/git")
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    calls = []
    monkeypatch.setattr(installer, "run", lambda args, **kw: calls.append(args))
    prefix = tmp_path / "installation with spaces á"
    record = installer.install(options(prefix))
    assert record["state"] == "installed" and record["autostart"] == "none"
    assert not (prefix / ".env").exists() and not (prefix / "data/private.txt").exists()
    config = json.loads((prefix / "client-config/mcp-stdio.json").read_text())
    assert config["mcpServers"]["odoo19-docs"]["command"] == str(prefix / ".venv/bin/python")
    parsed = tomllib.loads((prefix / "client-config/codex-http.toml").read_text())
    assert parsed["mcp_servers"]["odoo19-docs"]["url"] == "http://127.0.0.1:8019/mcp"
    assert any("--locked" in command for command in calls)
    assert not any(command[0] == "systemctl" for command in calls)
    # Reinstall is allowed only because this directory has our managed marker.
    assert installer.install(options(prefix))["state"] == "installed"


def test_install_never_overwrites_an_unmanaged_destination(tmp_path):
    (tmp_path / "valuable.txt").write_text("keep")
    with pytest.raises(ValueError, match="destino"):
        installer.install(options(tmp_path))
    assert (tmp_path / "valuable.txt").read_text() == "keep"


def test_failed_install_is_not_reported_as_complete(tmp_path, monkeypatch):
    prefix = tmp_path / "app"
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(installer.shutil, "which", lambda _: "/fake/git")
    monkeypatch.setattr(installer, "locate_uv", lambda *args: "/fake/uv")

    def fail(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(installer, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(options(prefix))
    assert json.loads((prefix / ".odoo19-install.json").read_text())["state"] == "installing"


def test_systemd_paths_are_quoted_and_specifiers_are_not_expanded(tmp_path):
    path = tmp_path / 'prefix with space %name $value "quote"'
    unit = installer.service_unit(path, path / "data", 8123)
    assert "WantedBy=default.target" in unit
    assert "streamable-http" in unit and '"8123"' in unit
    assert "%%name" in unit and "$$value" in unit
    assert '\\"quote\\"' in unit
    assert "0.0.0.0" not in unit and "Restart=on-failure" in unit
    with pytest.raises(ValueError):
        installer.unit_quote("/path\ninjected")


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd parser unavailable")
def test_generated_unit_is_accepted_by_systemd(tmp_path):
    prefix = tmp_path / "installation with spaces"
    python = prefix / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    unit = tmp_path / "odoo19-parser-test.service"
    unit.write_text(installer.service_unit(prefix, prefix / "data", 8019))
    result = subprocess.run(
        ["systemd-analyze", "verify", str(unit)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
