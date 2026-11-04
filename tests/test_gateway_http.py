from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import MCPError

from forge_agent_mcp.corpus.manifest import load_manifest
from forge_agent_mcp.gateway.auth import APPROVE_SCOPE, READ_SCOPE, WRITE_SCOPE
from tests.conftest import (
    EXPECTED_TOOLS,
    INVOICE_ONLY_FLAGS,
    MANIFEST_PATH,
    ONBOARDING_ONLY_FLAGS,
    OTHER_TENANT,
    READ_TOOLS,
    TENANT,
)


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
    """Wire-level probe.

    The FastMCP Client collapses a transport 403 into ``MCPError(-32603)``, so the
    status code and ``WWW-Authenticate`` challenge can only be asserted here.
    """

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


def _auth_denied(events) -> list[Any]:
    return [event for event in events if event.event == "auth_denied"]


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
        assert tool.input_schema["properties"]["case_id"]["minLength"] == 1
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


def test_invalid_token_is_rejected_with_401_and_no_victim_audit(gateway) -> None:
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
    assert gateway.store.list_audit(TENANT, "case_onb_ridgeline") == []


def test_expired_token_is_rejected_with_401(gateway) -> None:
    token = gateway.issuer.issue(
        subject="agent-expired",
        tenant_id=TENANT,
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


@pytest.mark.parametrize(
    ("tool_name", "required_scope", "arguments"),
    [
        (
            "record_extraction",
            WRITE_SCOPE,
            {
                "case_id": "case_onb_ridgeline",
                "document_id": "DOC-ONB-RIDGELINE-APP",
                "fields": [],
            },
        ),
        (
            "record_recommendation",
            WRITE_SCOPE,
            {
                "case_id": "case_onb_ridgeline",
                "recommendation": "approve",
                "rationale": "Read-only token must not write.",
                "conditions": [],
                "flags": [],
            },
        ),
        (
            "create_decision",
            APPROVE_SCOPE,
            {
                "case_id": "case_onb_ridgeline",
                "decision": "approve",
                "rationale": "Read-only token must not approve.",
                "conditions": [],
                "approval_token": "unused",
                "idempotency_key": "idem-readonly",
            },
        ),
    ],
)
def test_read_token_calling_a_write_tool_gets_403_with_challenge(
    gateway, tool_name: str, required_scope: str, arguments: dict[str, Any]
) -> None:
    token = gateway.token(subject="agent-readonly", scopes=[READ_SCOPE])

    with pytest.raises(MCPError):
        asyncio.run(_call(gateway, token, tool_name, arguments))

    response = _rpc(
        gateway,
        token,
        "tools/call",
        {"name": tool_name, "arguments": arguments},
        tool_name=tool_name,
    )

    assert response.status_code == 403
    challenge = response.headers.get("www-authenticate", "")
    assert 'error="insufficient_scope"' in challenge
    assert f'scope="{required_scope}"' in challenge
    assert json.loads(response.text)["error"] == "insufficient_scope"

    denied = _auth_denied(gateway.store.list_audit(TENANT, "case_onb_ridgeline", limit=50))
    assert denied
    assert denied[-1].tool == tool_name
    assert denied[-1].principal == "agent-readonly"
    assert denied[-1].detail["reason"] == "insufficient_scope"
    assert denied[-1].detail["target_tenant"] == TENANT


def test_write_scope_cannot_call_create_decision(gateway) -> None:
    token = gateway.token(subject="agent-writer", scopes=[READ_SCOPE, WRITE_SCOPE])
    arguments = {
        "case_id": "case_onb_ridgeline",
        "decision": "approve",
        "rationale": "Should require approval scope.",
        "conditions": [],
        "approval_token": "unused",
        "idempotency_key": "idem-write-only",
    }

    response = _rpc(
        gateway,
        token,
        "tools/call",
        {"name": "create_decision", "arguments": arguments},
        tool_name="create_decision",
    )

    assert response.status_code == 403
    assert 'scope="freight:approve"' in response.headers.get("www-authenticate", "")
    assert json.loads(response.text)["error"] == "insufficient_scope"


def test_cross_tenant_case_gets_403_and_an_auth_denied_audit_row(gateway) -> None:
    token = gateway.token(
        subject="agent-other-tenant",
        tenant_id=OTHER_TENANT,
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
    assert "insufficient_scope" not in response.headers.get("www-authenticate", "")

    caller_events = gateway.store.list_audit(OTHER_TENANT, "case_onb_ridgeline")
    denied = _auth_denied(caller_events)
    assert denied
    assert denied[-1].detail["reason"] == "tenant_mismatch"
    assert denied[-1].detail["caller_tenant"] == OTHER_TENANT
    assert denied[-1].detail["target_tenant"] == TENANT

    victim_events = gateway.store.list_audit(TENANT, "case_onb_ridgeline")
    assert victim_events == []


def test_each_tenant_reads_its_own_case_and_not_the_others(multi_tenant_gateway) -> None:
    northwind = multi_tenant_gateway.token(
        subject="agent-northwind",
        tenant_id=TENANT,
        scopes=[READ_SCOPE],
    )
    other = multi_tenant_gateway.token(
        subject="agent-other",
        tenant_id=OTHER_TENANT,
        scopes=[READ_SCOPE],
    )

    async def own_cases():
        async with _client(multi_tenant_gateway, northwind) as client:
            north_own = await client.call_tool_mcp("read_case", {"case_id": "case_onb_ridgeline"})
        async with _client(multi_tenant_gateway, other) as client:
            other_own = await client.call_tool_mcp("read_case", {"case_id": "case_onb_meridian"})
        return north_own, other_own

    north_own, other_own = asyncio.run(own_cases())
    assert north_own.is_error is False
    assert north_own.structured_content["data"]["tenant_id"] == TENANT
    assert other_own.is_error is False
    assert other_own.structured_content["data"]["tenant_id"] == OTHER_TENANT

    north_cross = _rpc(
        multi_tenant_gateway,
        northwind,
        "tools/call",
        {"name": "read_case", "arguments": {"case_id": "case_onb_meridian"}},
        tool_name="read_case",
    )
    other_cross = _rpc(
        multi_tenant_gateway,
        other,
        "tools/call",
        {"name": "read_case", "arguments": {"case_id": "case_onb_ridgeline"}},
        tool_name="read_case",
    )
    assert north_cross.status_code == 403
    assert json.loads(north_cross.text)["error"] == "tenant_mismatch"
    assert other_cross.status_code == 403
    assert json.loads(other_cross.text)["error"] == "tenant_mismatch"

    north_denied = _auth_denied(multi_tenant_gateway.store.list_audit(TENANT, "case_onb_meridian"))
    other_denied = _auth_denied(
        multi_tenant_gateway.store.list_audit(OTHER_TENANT, "case_onb_ridgeline")
    )
    assert north_denied[-1].detail["target_tenant"] == OTHER_TENANT
    assert other_denied[-1].detail["target_tenant"] == TENANT
    assert (
        _auth_denied(multi_tenant_gateway.store.list_audit(OTHER_TENANT, "case_onb_meridian")) == []
    )
    assert _auth_denied(multi_tenant_gateway.store.list_audit(TENANT, "case_onb_ridgeline")) == []


def test_policy_packs_are_case_type_specific(gateway) -> None:
    token = gateway.token(subject="agent-reader", scopes=[READ_SCOPE])

    async def scenario():
        async with _client(gateway, token) as client:
            onboarding = await client.call_tool_mcp("get_policy", {"case_id": "case_onb_ridgeline"})
            invoice = await client.call_tool_mcp("get_policy", {"case_id": "case_inv_ridgeline"})
            return onboarding, invoice

    onboarding, invoice = asyncio.run(scenario())
    assert onboarding.is_error is False
    assert invoice.is_error is False
    onboarding_codes = {flag["code"] for flag in onboarding.structured_content["data"]["flags"]}
    invoice_codes = {flag["code"] for flag in invoice.structured_content["data"]["flags"]}

    assert invoice_codes >= INVOICE_ONLY_FLAGS
    assert not INVOICE_ONLY_FLAGS & onboarding_codes
    assert onboarding_codes >= ONBOARDING_ONLY_FLAGS
    assert not ONBOARDING_ONLY_FLAGS & invoice_codes
    assert invoice.structured_content["data"]["case_type"] == "invoice"
    assert onboarding.structured_content["data"]["case_type"] == "onboarding"


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
            duplicate = await client.call_tool_mcp(
                "check_duplicate", {"case_id": "case_inv_cedar_pine"}
            )
            clean = await client.call_tool_mcp("check_duplicate", {"case_id": "case_inv_ridgeline"})
            hash_duplicate = await client.call_tool_mcp(
                "check_duplicate", {"case_id": "case_inv_harborline"}
            )
            envelope = await client.call_tool_mcp(
                "get_audit", {"case_id": "case_onb_ridgeline", "limit": 50}
            )
            return {
                "case": case,
                "document": document,
                "duplicate": duplicate,
                "clean": clean,
                "hash_duplicate": hash_duplicate,
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

    duplicate = results["duplicate"].structured_content["data"]
    assert duplicate["duplicate"] is True
    assert duplicate["matches"][0]["kind"] == "invoice_number"
    assert duplicate["matches"][0]["invoice_number"] == "INV-CED-1002"

    clean = results["clean"].structured_content["data"]
    assert clean["duplicate"] is False
    assert clean["matches"] == []

    hash_duplicate = results["hash_duplicate"].structured_content["data"]
    assert hash_duplicate["duplicate"] is True
    assert any(match["kind"] == "document_hash" for match in hash_duplicate["matches"])

    events = results["audit"].structured_content["data"]["events"]
    assert {event["tool"] for event in events} >= {"read_case", "get_document"}
    assert all(event["event"] == "read" for event in events)
    assert {event["principal"] for event in events} == {"agent-reader"}

    duplicate_events = gateway.store.list_audit(TENANT, "case_inv_cedar_pine")
    assert any(
        event.tool == "check_duplicate" and event.principal == "agent-reader"
        for event in duplicate_events
    )


def test_read_case_summary_does_not_leak_corpus_notes(gateway) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    notes = {case.case_id: case.notes for case in manifest.cases if case.notes}
    token = gateway.token(subject="agent-reader", scopes=[READ_SCOPE])

    async def scenario() -> dict[str, str]:
        summaries = {}
        async with _client(gateway, token) as client:
            for case_id in notes:
                result = await client.call_tool_mcp("read_case", {"case_id": case_id})
                assert result.is_error is False
                summaries[case_id] = result.structured_content["data"]["summary"]
        return summaries

    summaries = asyncio.run(scenario())
    for case_id, note in notes.items():
        assert note not in summaries[case_id]
        assert summaries[case_id]


def test_write_tools_store_state_and_append_write_audit(gateway) -> None:
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
        "flags": [
            {
                "code": "authority_young",
                "severity": "medium",
                "rule": "authority granted within 6 months",
                "details": "Authority age is a rule-engine concern, not a gateway check.",
                "evidence": {
                    "document_id": "DOC-ONB-RIDGELINE-MC",
                    "page": 1,
                    "bbox": [188.0, 126.0, 330.0, 139.0],
                },
            }
        ],
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
    assert recommendation_result.structured_content["data"]["flags"][0]["code"] == (
        "authority_young"
    )

    case = case_result.structured_content["data"]
    assert case["status"] == "extracting"
    assert case["extraction_count"] == 1
    assert case["recommendation"]["recommendation"] == "approve"
    assert case["flags"][0]["code"] == "authority_young"
    assert case["documents"][0]["status"] == "extracted"

    events = gateway.store.list_audit(TENANT, "case_onb_ridgeline")
    writes = [event for event in events if event.event == "write"]
    assert {event.tool for event in writes} == {
        "record_extraction",
        "record_recommendation",
    }
    assert {event.principal for event in writes} == {"agent-writer"}
    assert all(event.input_hash and len(event.input_hash) == 64 for event in writes)
    assert all(event.output_hash and len(event.output_hash) == 64 for event in writes)


@pytest.mark.parametrize(
    ("evidence", "expected_code"),
    [
        ({}, "invalid_request"),
        (
            {
                "document_id": "DOC-INV-RIDGELINE-INV",
                "page": 1,
                "bbox": [1.0, 1.0, 2.0, 2.0],
            },
            "invalid_evidence",
        ),
        (
            {
                "document_id": "DOC-ONB-RIDGELINE-MC",
                "page": 99,
                "bbox": [1.0, 1.0, 2.0, 2.0],
            },
            "invalid_evidence",
        ),
        (
            {
                "document_id": "DOC-ONB-RIDGELINE-MC",
                "page": 1,
                "bbox": [-1.0, 0.0, 10.0, 10.0],
            },
            "invalid_evidence",
        ),
        (
            {
                "document_id": "DOC-ONB-RIDGELINE-MC",
                "page": 1,
                "bbox": [0.0, 0.0, 5000.0, 10.0],
            },
            "invalid_evidence",
        ),
    ],
)
def test_record_recommendation_rejects_invalid_evidence(
    gateway, evidence: dict[str, Any], expected_code: str
) -> None:
    token = gateway.token(subject="agent-writer", scopes=[READ_SCOPE, WRITE_SCOPE])

    result = asyncio.run(
        _call(
            gateway,
            token,
            "record_recommendation",
            {
                "case_id": "case_onb_ridgeline",
                "recommendation": "approve",
                "rationale": "Evidence must point into the case.",
                "conditions": [],
                "flags": [
                    {
                        "code": "authority_young",
                        "severity": "medium",
                        "rule": "test",
                        "details": "test",
                        "evidence": evidence,
                    }
                ],
            },
        )
    )

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert result.structured_content["error_code"] == expected_code
    assert result.structured_content["data"] is None


def test_record_recommendation_does_not_apply_policy_or_state(
    gateway,
) -> None:
    token = gateway.token(subject="agent-writer", scopes=[READ_SCOPE, WRITE_SCOPE])

    async def scenario():
        async with _client(gateway, token) as client:
            written = await client.call_tool_mcp(
                "record_recommendation",
                {
                    "case_id": "case_onb_ridgeline",
                    "recommendation": "hold",
                    "rationale": "The rule engine owns severity and state.",
                    "conditions": ["Manual review."],
                    "flags": [
                        {
                            "code": "authority_young",
                            "severity": "hard_stop",
                            "rule": "caller-supplied severity",
                            "details": "The gateway must not rewrite this.",
                            "evidence": {
                                "document_id": "DOC-ONB-RIDGELINE-MC",
                                "page": 1,
                                "bbox": [188.0, 126.0, 330.0, 139.0],
                            },
                        }
                    ],
                },
            )
            case = await client.call_tool_mcp("read_case", {"case_id": "case_onb_ridgeline"})
            return written, case

    written, case = asyncio.run(scenario())

    assert written.is_error is False
    assert written.structured_content["data"]["flags"][0]["severity"] == "hard_stop"
    data = case.structured_content["data"]
    assert data["status"] == "pending_review"
    assert data["flags"][0]["severity"] == "hard_stop"


def test_business_errors_return_is_error_envelopes_and_denied_audit(gateway) -> None:
    token = gateway.token(subject="agent-writer", scopes=[READ_SCOPE, WRITE_SCOPE])

    async def scenario():
        async with _client(gateway, token) as client:
            unknown_document = await client.call_tool_mcp(
                "get_document",
                {
                    "case_id": "case_onb_ridgeline",
                    "document_id": "DOC-MISSING",
                },
            )
            invalid_citation = await client.call_tool_mcp(
                "record_extraction",
                {
                    "case_id": "case_onb_ridgeline",
                    "document_id": "DOC-ONB-RIDGELINE-APP",
                    "fields": [
                        {
                            "field": "application_kyc.legal_name",
                            "value": "Ridgeline Haulage LLC",
                            "citation": {
                                "document_id": "DOC-ONB-RIDGELINE-MC",
                                "page": 1,
                                "bbox": [188.0, 126.0, 330.0, 139.0],
                            },
                        }
                    ],
                },
            )
            return unknown_document, invalid_citation

    unknown_document, invalid_citation = asyncio.run(scenario())

    assert unknown_document.is_error is True
    assert unknown_document.structured_content["ok"] is False
    assert unknown_document.structured_content["error_code"] == "document_not_found"
    assert unknown_document.structured_content["data"] is None

    assert invalid_citation.is_error is True
    assert invalid_citation.structured_content["error_code"] == "invalid_citation"

    denied = [
        event
        for event in gateway.store.list_audit(TENANT, "case_onb_ridgeline")
        if event.event == "write_denied"
    ]
    assert denied
    assert denied[-1].detail["error_code"] == "invalid_citation"


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("read_case", {"case_id": ""}),
        ("get_document", {"case_id": "", "document_id": "DOC-ONB-RIDGELINE-APP"}),
        ("get_policy", {"case_id": ""}),
        ("check_duplicate", {"case_id": ""}),
        ("get_audit", {"case_id": "case_onb_ridgeline", "limit": 0}),
    ],
)
def test_invalid_inputs_return_error_envelopes(
    gateway, tool_name: str, arguments: dict[str, Any]
) -> None:
    token = gateway.token(subject="agent-reader", scopes=[READ_SCOPE])

    result = asyncio.run(_call(gateway, token, tool_name, arguments))

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert result.structured_content["error_code"] == "invalid_request"
    assert result.structured_content["data"] is None


@pytest.mark.parametrize(
    "tool_name",
    ["read_case", "get_document", "get_policy", "check_duplicate", "get_audit"],
)
def test_missing_case_returns_a_case_not_found_envelope(gateway, tool_name: str) -> None:
    token = gateway.token(subject="agent-reader", scopes=[READ_SCOPE])
    arguments: dict[str, Any] = {"case_id": "case_missing"}
    if tool_name == "get_document":
        arguments["document_id"] = "DOC-MISSING"

    result = asyncio.run(_call(gateway, token, tool_name, arguments))

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert result.structured_content["error_code"] == "case_not_found"
    assert result.structured_content["data"] is None


def test_create_decision_refuses_a_missing_approval_token(gateway) -> None:
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
                    "approval_token": "",
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

    events = gateway.store.list_audit(TENANT, "case_onb_ridgeline")
    denied = [event for event in events if event.event == "write_denied"]
    assert denied[-1].tool == "create_decision"
    assert denied[-1].detail["error_code"] == "approval_required"
