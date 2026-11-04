from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import MCPError

from forge_agent_mcp.gateway.auth import APPROVE_SCOPE, READ_SCOPE, WRITE_SCOPE

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
READ_TOOLS = {"read_case", "get_document", "get_policy", "check_duplicate", "get_audit"}
WRITE_TOOLS = {"record_extraction", "record_recommendation", "create_decision"}


def _client(gateway, token: str) -> Client:
    return Client(StreamableHttpTransport(gateway.url, auth=token))


async def _call(gateway, token: str, name: str, arguments: dict[str, Any]):
    async with _client(gateway, token) as client:
        return await client.call_tool_mcp(name, arguments)


def _rpc(
    gateway,
    token: str,
    method: str,
    params: dict[str, Any],
    *,
    tool_name: str | None = None,
) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
    }
    if tool_name:
        headers["Mcp-Name"] = tool_name
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    with httpx.Client(timeout=10) as client:
        return client.post(gateway.url, json=body, headers=headers)


def test_tools_list_is_deterministic_typed_and_annotated(gateway) -> None:
    token = gateway.token(
        subject="agent-admin",
        scopes=[READ_SCOPE, WRITE_SCOPE, APPROVE_SCOPE],
    )

    async def scenario():
        async with _client(gateway, token) as client:
            return await client.list_tools(), await client.list_tools()

    first, second = asyncio.run(scenario())

    assert [tool.name for tool in first] == EXPECTED_TOOLS
    assert [tool.name for tool in second] == EXPECTED_TOOLS
    for tool in first:
        assert tool.description
        assert tool.input_schema["type"] == "object"
        assert "case_id" in tool.input_schema["properties"]
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "data" in tool.output_schema["properties"]
        assert tool.annotations is not None
        expected_read_only = tool.name in READ_TOOLS
        assert tool.annotations.read_only_hint is expected_read_only
        assert tool.annotations.open_world_hint is False
    decision = next(tool for tool in first if tool.name == "create_decision")
    assert decision.input_schema["required"] == [
        "case_id",
        "decision",
        "rationale",
        "conditions",
        "approval_token",
        "idempotency_key",
    ]
    assert decision.annotations.destructive_hint is True


def test_invalid_token_is_rejected_with_401(gateway) -> None:
    with pytest.raises(MCPError):
        asyncio.run(
            _call(gateway, "not-a-real-token", "read_case", {"case_id": "case_onb_ridgeline"})
        )

    response = _rpc(
        gateway,
        "not-a-real-token",
        "tools/call",
        {"name": "read_case", "arguments": {"case_id": "case_onb_ridgeline"}},
        tool_name="read_case",
    )

    assert response.status_code == 401
    assert "invalid_token" in response.headers.get("www-authenticate", "")
    assert json.loads(response.text)["error"] == "invalid_token"


def test_expired_token_is_rejected_with_401(gateway) -> None:
    token = gateway.issuer.issue(
        subject="agent-expired",
        tenant_id="tenant_northwind",
        scopes=[READ_SCOPE],
        ttl_seconds=-5,
    )

    response = _rpc(
        gateway,
        token,
        "tools/call",
        {"name": "read_case", "arguments": {"case_id": "case_onb_ridgeline"}},
        tool_name="read_case",
    )

    assert response.status_code == 401
    assert "invalid_token" in response.headers.get("www-authenticate", "")


def test_read_token_calling_a_write_tool_gets_403_with_challenge(gateway) -> None:
    token = gateway.token(subject="agent-readonly", scopes=[READ_SCOPE])
    arguments = {
        "case_id": "case_onb_ridgeline",
        "document_id": "DOC-ONB-RIDGELINE-APP",
        "fields": [],
    }

    with pytest.raises(MCPError):
        asyncio.run(_call(gateway, token, "record_extraction", arguments))

    response = _rpc(
        gateway,
        token,
        "tools/call",
        {"name": "record_extraction", "arguments": arguments},
        tool_name="record_extraction",
    )

    assert response.status_code == 403
    challenge = response.headers.get("www-authenticate", "")
    assert 'error="insufficient_scope"' in challenge
    assert f'scope="{WRITE_SCOPE}"' in challenge
    assert json.loads(response.text)["error"] == "insufficient_scope"

    denied = [
        event
        for event in gateway.store.list_audit("tenant_northwind", "case_onb_ridgeline", limit=50)
        if event.event == "auth_denied"
    ]
    assert denied
    assert denied[-1].tool == "record_extraction"
    assert denied[-1].principal == "agent-readonly"
    assert denied[-1].detail["reason"] == "insufficient_scope"


def test_cross_tenant_case_gets_403_and_an_auth_denied_audit_row(gateway) -> None:
    token = gateway.token(
        subject="agent-other-tenant",
        tenant_id="tenant_other",
        scopes=[READ_SCOPE],
    )

    with pytest.raises(MCPError):
        asyncio.run(_call(gateway, token, "read_case", {"case_id": "case_onb_ridgeline"}))

    response = _rpc(
        gateway,
        token,
        "tools/call",
        {"name": "read_case", "arguments": {"case_id": "case_onb_ridgeline"}},
        tool_name="read_case",
    )

    assert response.status_code == 403
    assert json.loads(response.text)["error"] == "tenant_mismatch"

    events = gateway.store.list_audit("tenant_northwind", "case_onb_ridgeline")
    denied = [event for event in events if event.event == "auth_denied"]
    assert denied
    assert denied[-1].detail["reason"] == "tenant_mismatch"
    assert denied[-1].detail["caller_tenant"] == "tenant_other"


def test_read_tools_return_typed_results_and_append_read_audit(gateway) -> None:
    token = gateway.token(subject="agent-reader", scopes=[READ_SCOPE])

    async def scenario() -> dict[str, Any]:
        async with _client(gateway, token) as client:
            case = await client.call_tool_mcp("read_case", {"case_id": "case_onb_ridgeline"})
            document = await client.call_tool_mcp(
                "get_document",
                {
                    "case_id": "case_onb_ridgeline",
                    "document_id": "DOC-ONB-RIDGELINE-APP",
                },
            )
            policy = await client.call_tool_mcp("get_policy", {"case_id": "case_onb_ridgeline"})
            duplicate = await client.call_tool_mcp(
                "check_duplicate", {"case_id": "case_inv_cedar_pine"}
            )
            clean = await client.call_tool_mcp("check_duplicate", {"case_id": "case_inv_ridgeline"})
            envelope = await client.call_tool_mcp(
                "get_audit", {"case_id": "case_onb_ridgeline", "limit": 50}
            )
            return {
                "case": case,
                "document": document,
                "policy": policy,
                "duplicate": duplicate,
                "clean": clean,
                "audit": envelope,
            }

    results = asyncio.run(scenario())

    for result in results.values():
        assert result.is_error is False

    case = results["case"].structured_content
    assert case["ok"] is True
    assert case["data"]["case_id"] == "case_onb_ridgeline"
    assert case["data"]["carrier"]["legal_name"] == "Ridgeline Haulage LLC"
    assert len(case["data"]["documents"]) == 5
    assert case["data"]["recommendation"] is None

    document = results["document"].structured_content["data"]
    assert document["document_type"] == "application_kyc"
    assert "Ridgeline Haulage LLC" in document["text"]
    assert document["bboxes"]
    assert document["content_hash"]

    policy = results["policy"].structured_content["data"]
    assert policy["case_type"] == "onboarding"
    assert policy["thresholds"]["min_auto_liability"] == 1_000_000
    assert any(flag["code"] == "insurance_expired" for flag in policy["flags"])

    duplicate = results["duplicate"].structured_content["data"]
    assert duplicate["duplicate"] is True
    assert duplicate["matches"][0]["kind"] == "invoice_number"
    assert duplicate["matches"][0]["invoice_number"] == "INV-CED-1002"

    clean = results["clean"].structured_content["data"]
    assert clean["duplicate"] is False
    assert clean["matches"] == []

    events = results["audit"].structured_content["data"]["events"]
    assert {event["tool"] for event in events} >= {
        "read_case",
        "get_document",
        "get_policy",
    }
    assert all(event["event"] == "read" for event in events)
    assert {event["principal"] for event in events} == {"agent-reader"}

    duplicate_events = gateway.store.list_audit("tenant_northwind", "case_inv_cedar_pine")
    assert any(
        event.tool == "check_duplicate" and event.principal == "agent-reader"
        for event in duplicate_events
    )


def test_write_tools_update_case_state_and_append_write_audit(gateway) -> None:
    token = gateway.token(subject="agent-writer", scopes=[READ_SCOPE, WRITE_SCOPE])
    extraction = {
        "case_id": "case_onb_ridgeline",
        "document_id": "DOC-ONB-RIDGELINE-APP",
        "fields": [
            {
                "field": "application_kyc.legal_name",
                "value": "Ridgeline Haulage LLC",
                "citation": {
                    "document_id": "DOC-ONB-RIDGELINE-APP",
                    "page": 1,
                    "bbox": [188.0, 126.0, 330.0, 139.0],
                },
            }
        ],
    }
    recommendation = {
        "case_id": "case_onb_ridgeline",
        "recommendation": "approve",
        "rationale": "Clean packet.",
        "conditions": [],
        "flags": [],
    }

    async def scenario() -> tuple[Any, Any, Any]:
        async with _client(gateway, token) as client:
            written_extraction = await client.call_tool_mcp("record_extraction", extraction)
            written_recommendation = await client.call_tool_mcp(
                "record_recommendation", recommendation
            )
            case = await client.call_tool_mcp("read_case", {"case_id": "case_onb_ridgeline"})
            return written_extraction, written_recommendation, case

    extraction_result, recommendation_result, case_result = asyncio.run(scenario())

    assert extraction_result.is_error is False
    assert extraction_result.structured_content["data"]["extraction_id"].startswith("ext_")
    assert extraction_result.structured_content["data"]["recorded_by"] == "agent-writer"
    assert recommendation_result.is_error is False
    assert recommendation_result.structured_content["data"]["recommendation"] == "approve"

    case = case_result.structured_content["data"]
    assert case["status"] == "pending_approval"
    assert case["extraction_count"] == 1
    assert case["recommendation"]["recommendation"] == "approve"
    assert case["documents"][0]["status"] == "extracted"

    events = gateway.store.list_audit("tenant_northwind", "case_onb_ridgeline")
    writes = [event for event in events if event.event == "write"]
    assert {event.tool for event in writes} == {
        "record_extraction",
        "record_recommendation",
    }
    assert {event.principal for event in writes} == {"agent-writer"}
    assert all(event.input_hash and len(event.input_hash) == 64 for event in writes)
    assert all(event.output_hash and len(event.output_hash) == 64 for event in writes)


def test_business_errors_return_is_error_envelopes_and_denied_audit(gateway) -> None:
    token = gateway.token(subject="agent-writer", scopes=[READ_SCOPE, WRITE_SCOPE])

    async def scenario():
        async with _client(gateway, token) as client:
            unknown_case = await client.call_tool_mcp("read_case", {"case_id": "case_missing"})
            unknown_document = await client.call_tool_mcp(
                "get_document",
                {
                    "case_id": "case_onb_ridgeline",
                    "document_id": "DOC-MISSING",
                },
            )
            invalid_flag = await client.call_tool_mcp(
                "record_recommendation",
                {
                    "case_id": "case_onb_ridgeline",
                    "recommendation": "approve",
                    "rationale": "Test invalid flag.",
                    "conditions": [],
                    "flags": [
                        {
                            "code": "not_a_real_flag",
                            "severity": "low",
                            "rule": "test",
                            "details": "test",
                            "evidence": {},
                        }
                    ],
                },
            )
            return unknown_case, unknown_document, invalid_flag

    unknown_case, unknown_document, invalid_flag = asyncio.run(scenario())

    assert unknown_case.is_error is True
    assert unknown_case.structured_content["ok"] is False
    assert unknown_case.structured_content["error_code"] == "case_not_found"
    assert unknown_case.structured_content["data"] is None

    assert unknown_document.is_error is True
    assert unknown_document.structured_content["error_code"] == "document_not_found"

    assert invalid_flag.is_error is True
    assert invalid_flag.structured_content["error_code"] == "invalid_flag"

    denied = [
        event
        for event in gateway.store.list_audit("tenant_northwind", "case_onb_ridgeline")
        if event.event == "write_denied"
    ]
    assert denied
    assert denied[-1].detail["error_code"] == "invalid_flag"


def test_create_decision_refuses_with_approval_required_until_ticket_6(gateway) -> None:
    token = gateway.token(subject="agent-approver", scopes=[READ_SCOPE, APPROVE_SCOPE])

    async def scenario():
        async with _client(gateway, token) as client:
            result = await client.call_tool_mcp(
                "create_decision",
                {
                    "case_id": "case_onb_ridgeline",
                    "decision": "approve",
                    "rationale": "Looks clean.",
                    "conditions": [],
                    "approval_token": "not-a-real-approval-token",
                    "idempotency_key": "idem-1",
                },
            )
            case = await client.call_tool_mcp("read_case", {"case_id": "case_onb_ridgeline"})
            return result, case

    result, case = asyncio.run(scenario())

    assert result.is_error is True
    assert result.structured_content["ok"] is False
    assert result.structured_content["error_code"] == "approval_required"
    assert "approval token" in result.structured_content["message"]
    assert result.structured_content["data"] is None
    assert case.structured_content["data"]["decision"] is None

    events = gateway.store.list_audit("tenant_northwind", "case_onb_ridgeline")
    denied = [event for event in events if event.event == "write_denied"]
    assert denied[-1].tool == "create_decision"
    assert denied[-1].detail["error_code"] == "approval_required"
