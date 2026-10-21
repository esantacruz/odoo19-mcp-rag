from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from starlette.testclient import TestClient

from src import server
from src.config import ROOT
from src.probe import probe
from src.retriever import Retriever


def test_health_and_unready_index(tmp_path, monkeypatch):
    from src.config import Settings

    monkeypatch.setattr(server, "retriever", lambda: Retriever(Settings(data_dir=tmp_path)))
    with TestClient(server.mcp.streamable_http_app()) as client:
        assert client.get("/healthz").json()["service"] == "Odoo19Docs"
        response = client.get("/readyz")
        assert response.status_code == 503 and not response.json()["ready"]


def test_cli_rejects_public_binding():
    result = subprocess.run(
        [sys.executable, "-m", "src.server", "--transport", "streamable-http", "--host", "0.0.0.0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2 and "invalid choice" in result.stderr


@pytest.mark.integration
async def test_http_real_service_and_two_clients():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "src.server", "--transport", "streamable-http", "--port", str(port)],
        cwd=ROOT,
        env={**os.environ, "ODOO_RAG_OFFLINE": "true"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            if process.poll() is not None:
                pytest.fail("HTTP service exited before becoming ready")
            try:
                with urlopen(f"http://127.0.0.1:{port}/readyz", timeout=1) as response:
                    assert response.status == 200
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail("HTTP service startup timed out")
                time.sleep(0.2)
        # Separate MCP sessions share one loaded model and one Chroma process.
        first = await probe(f"http://127.0.0.1:{port}/mcp")
        second = await probe(f"http://127.0.0.1:{port}/mcp", query="computed fields dependencies")
        assert first["ready"] and second["ready"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
