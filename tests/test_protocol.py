"""A real MCP client launches the real server; no in-process tool shortcuts."""

import os
import sys
from datetime import timedelta

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.config import ROOT


async def test_protocol_handshake_tools_and_missing_index(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "src/server.py")],
        cwd=str(tmp_path),
        env={**os.environ, "ODOO_RAG_DATA_DIR": str(tmp_path / "no-index")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=45)
        ) as session:
            init = await session.initialize()
            assert init.serverInfo.name == "Odoo19Docs"
            listed = await session.list_tools()
            assert {t.name for t in listed.tools} == {
                "search_odoo19_docs",
                "get_odoo_topic_context",
            }
            tool = next(t for t in listed.tools if t.name == "search_odoo19_docs")
            assert set(tool.inputSchema["properties"]) == {"query", "max_results", "category"}
            result = await session.call_tool("search_odoo19_docs", {"query": "OWL hooks"})
            assert result.isError
            assert "Índice ausente" in result.content[0].text


@pytest.mark.integration
async def test_real_corpus_over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "src.server"], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=90)
        ) as session:
            await session.initialize()
            search = await session.call_tool(
                "search_odoo19_docs", {"query": "useBus OWL hooks", "category": "developer"}
            )
            assert not search.isError
            assert "useBus" in search.content[0].text
            assert "https://www.odoo.com/documentation/19.0/" in search.content[0].text
            context = await session.call_tool(
                "get_odoo_topic_context", {"topic": "record rules syntax"}
            )
            assert not context.isError and "Record Rules" in context.content[0].text
            invalid = await session.call_tool(
                "search_odoo19_docs", {"query": "x", "max_results": 0}
            )
            assert invalid.isError
