from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from forge_agent_mcp.gateway.store import GatewayStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "corpus" / "manifest.json"

EXPECTED_TOOLS = [
    "read_case",
    "get_document",
    "get_policy",
    "check_duplicate",
    "record_extraction",
    "record_recommendation",
    "create_decision",
    "get_audit",
]


def test_stdio_serves_the_same_tools_without_auth_and_warns(
    seeded_db: Path, tmp_path: Path
) -> None:
    log_file = tmp_path / "stdio-stderr.log"
    transport = StdioTransport(
        sys.executable,
        [
            "-m",
            "forge_agent_mcp.gateway",
            "serve",
            "--transport",
            "stdio",
            "--db",
            str(seeded_db),
            "--manifest",
            str(MANIFEST_PATH),
        ],
        keep_alive=False,
        log_file=log_file,
    )

    async def scenario():
        async with Client(transport) as client:
            tools = await client.list_tools()
            read = await client.call_tool_mcp("read_case", {"case_id": "case_onb_ridgeline"})
            write = await client.call_tool_mcp(
                "record_recommendation",
                {
                    "case_id": "case_onb_ridgeline",
                    "recommendation": "approve",
                    "rationale": "Trusted local stdio call.",
                    "conditions": [],
                    "flags": [],
                },
            )
            return tools, read, write

    tools, read, write = asyncio.run(scenario())

    assert [tool.name for tool in tools] == EXPECTED_TOOLS
    assert read.is_error is False
    assert read.structured_content["data"]["case_id"] == "case_onb_ridgeline"
    assert write.is_error is False
    assert write.structured_content["data"]["recorded_by"] == "stdio-local"

    store = GatewayStore(seeded_db)
    try:
        events = store.list_audit("tenant_northwind", "case_onb_ridgeline")
    finally:
        store.close()
    assert any(event.principal == "stdio-local" for event in events)

    stderr = log_file.read_text(encoding="utf-8")
    assert "skips authentication" in stderr
