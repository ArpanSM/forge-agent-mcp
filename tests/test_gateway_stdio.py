from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from forge_agent_mcp.gateway.store import GatewayStore
from tests.conftest import APPROVAL_SECRET, EXPECTED_TOOLS, MANIFEST_PATH, PROJECT_ROOT, TENANT


def test_stdio_serves_the_same_tools_without_auth_and_warns(
    seeded_db: Path, tmp_path: Path
) -> None:
    """Run the stdio transport as a subprocess.

    stdio has no HTTP surface and FastMCP marks it trusted, so a real subprocess
    is the only way to prove that the same gateway skips auth and scopes there.
    """

    log_file = tmp_path / "stdio-stderr.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
        "FORGE_APPROVAL_SECRET": APPROVAL_SECRET,
    }
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
        env=env,
        cwd=str(PROJECT_ROOT),
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
        events = store.list_audit(TENANT, "case_onb_ridgeline")
    finally:
        store.close()
    assert any(event.principal == "stdio-local" for event in events)

    stderr = log_file.read_text(encoding="utf-8")
    assert "skips authentication" in stderr
