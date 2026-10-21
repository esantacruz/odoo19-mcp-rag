"""Real MCP-over-HTTP smoke test for the installed service."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def probe(url: str, query: str = "useBus OWL hooks") -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The service probe only accepts loopback HTTP URLs")
    async with (
        httpx.AsyncClient(timeout=60) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, _),
    ):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=60)
        ) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            if names != ["get_odoo_topic_context", "search_odoo19_docs"]:
                raise RuntimeError(f"Unexpected MCP tools: {names}")
            result = await session.call_tool(
                "search_odoo19_docs",
                {
                    "query": query,
                    "max_results": 1,
                    "category": "developer",
                },
            )
            if result.isError:
                raise RuntimeError("MCP tool returned an error")
            content = "\n".join(block.text for block in result.content if block.type == "text")
            if "https://www.odoo.com/documentation/19.0/" not in content:
                raise RuntimeError("MCP result does not contain an official Odoo source")
            return {
                "ready": True,
                "server": initialized.serverInfo.name,
                "url": url,
                "tools": names,
                "cited_result": True,
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8019/mcp")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.url)), indent=2))


if __name__ == "__main__":
    main()
