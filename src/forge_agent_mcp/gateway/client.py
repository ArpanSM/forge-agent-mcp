"""Small local client: list tools, read a case and show the wire-level 403."""

from __future__ import annotations

import asyncio
import json

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

DEFAULT_CASE_ID = "case_onb_ridgeline"


def _raw_write_probe(url: str, token: str, case_id: str) -> httpx.Response:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "record_extraction",
            "arguments": {"case_id": case_id, "document_id": "DOC-IGNORED", "fields": []},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "record_extraction",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=10) as client:
        return client.post(url, json=body, headers=headers)


async def run_demo(
    *,
    url: str,
    read_token: str,
    admin_token: str,
    case_id: str = DEFAULT_CASE_ID,
) -> int:
    async with Client(StreamableHttpTransport(url, auth=admin_token)) as client:
        tools = await client.list_tools()
    print("tools/list (all scopes):")
    for tool in tools:
        print(f"  - {tool.name}")

    async with Client(StreamableHttpTransport(url, auth=read_token)) as client:
        visible = await client.list_tools()
        print("\ntools/list (freight:read token):")
        print("  " + ", ".join(tool.name for tool in visible))
        result = await client.call_tool_mcp("read_case", {"case_id": case_id})

    print(f"\nread_case({case_id}) isError={result.is_error}")
    print(json.dumps(result.structured_content, indent=2, sort_keys=True))

    response = _raw_write_probe(url, read_token, case_id)
    print("\nrecord_extraction with the same read token (raw HTTP):")
    print(f"  status: {response.status_code}")
    print(f"  WWW-Authenticate: {response.headers.get('www-authenticate')}")
    print(f"  body: {response.text}")

    return 0 if not result.is_error and response.status_code == 403 else 1


def main(
    *,
    url: str,
    read_token: str,
    admin_token: str,
    case_id: str = DEFAULT_CASE_ID,
) -> int:
    return asyncio.run(
        run_demo(
            url=url,
            read_token=read_token,
            admin_token=admin_token,
            case_id=case_id,
        )
    )


__all__ = ["DEFAULT_CASE_ID", "main", "run_demo"]
