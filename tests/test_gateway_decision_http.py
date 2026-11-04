from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from forge_agent_mcp.corpus.vocab import Decision
from forge_agent_mcp.gateway.write_demo import run_write_demo
from tests.conftest import APPROVER, TENANT

CASE_ID = "case_onb_ridgeline"
PRINCIPAL = "agent-approver"


def _client(gateway, token: str) -> Client:
    return Client(StreamableHttpTransport(gateway.url, auth=token))


async def _call(gateway, token: str, name: str, arguments: dict[str, Any]):
    async with _client(gateway, token) as client:
        return await client.call_tool_mcp(name, arguments)


def _decision_call(
    *,
    decision: Decision = "approve",
    rationale: str = "Clean packet.",
    conditions: list[str] | None = None,
    approval_token: str = "",
    idempotency_key: str = "idem-1",
    case_id: str = CASE_ID,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "decision": decision,
        "rationale": rationale,
        "conditions": conditions or [],
        "approval_token": approval_token,
        "idempotency_key": idempotency_key,
    }


def _approval(
    gateway,
    *,
    decision: Decision = "approve",
    rationale: str = "Clean packet.",
    conditions: list[str] | None = None,
    tenant_id: str = TENANT,
    case_id: str = CASE_ID,
    ttl_seconds: int | None = 600,
    jti: str | None = None,
) -> str:
    return gateway.approval.issue_for_decision(
        approver=APPROVER,
        tenant_id=tenant_id,
        case_id=case_id,
        decision=decision,
        rationale=rationale,
        conditions=conditions or [],
        ttl_seconds=ttl_seconds,
        jti=jti,
    )


def _approver_token(gateway) -> str:
    return gateway.token(
        subject=PRINCIPAL,
        scopes=["freight:read", "freight:approve"],
    )


def _decisions(gateway) -> int:
    return gateway.store.count_decisions(TENANT, CASE_ID)


def _denied(gateway) -> list[Any]:
    return [
        event
        for event in gateway.store.list_audit(TENANT, CASE_ID)
        if event.event == "write_denied"
    ]


def test_missing_approval_is_refused_with_only_an_audit_row(gateway) -> None:
    result = asyncio.run(
        _call(gateway, _approver_token(gateway), "create_decision", _decision_call())
    )

    assert result.is_error is True
    assert result.structured_content["ok"] is False
    assert result.structured_content["error_code"] == "approval_required"
    assert result.structured_content["data"] is None
    assert _decisions(gateway) == 0
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 0
    assert gateway.store.get_idempotency(TENANT, PRINCIPAL, "create_decision", "idem-1") is None

    denied = _denied(gateway)
    assert len(denied) == 1
    assert denied[0].tool == "create_decision"
    assert denied[0].principal == PRINCIPAL
    assert denied[0].detail["error_code"] == "approval_required"


def test_malformed_approval_is_refused_with_only_an_audit_row(gateway) -> None:
    result = asyncio.run(
        _call(
            gateway,
            _approver_token(gateway),
            "create_decision",
            _decision_call(approval_token="not-a-real-token"),
        )
    )

    assert result.is_error is True
    assert result.structured_content["error_code"] == "approval_malformed"
    assert _decisions(gateway) == 0
    denied = _denied(gateway)
    assert denied[-1].detail["error_code"] == "approval_malformed"


def test_expired_approval_is_refused_with_only_an_audit_row(gateway) -> None:
    result = asyncio.run(
        _call(
            gateway,
            _approver_token(gateway),
            "create_decision",
            _decision_call(approval_token=_approval(gateway, ttl_seconds=-5)),
        )
    )

    assert result.is_error is True
    assert result.structured_content["error_code"] == "approval_expired"
    assert _decisions(gateway) == 0
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 0
    assert _denied(gateway)[-1].detail["error_code"] == "approval_expired"


def test_approval_bound_to_another_case_decision_or_payload_is_refused(gateway) -> None:
    wrong_case = _approval(gateway, case_id="case_inv_ridgeline")
    wrong_decision = _approval(gateway, decision="decline")
    wrong_payload = _approval(gateway, rationale="A different rationale.")

    calls = [
        _decision_call(approval_token=wrong_case, idempotency_key="idem-case"),
        _decision_call(approval_token=wrong_decision, idempotency_key="idem-decision"),
        _decision_call(approval_token=wrong_payload, idempotency_key="idem-payload"),
    ]

    async def scenario():
        results = []
        async with _client(gateway, _approver_token(gateway)) as client:
            for arguments in calls:
                results.append(await client.call_tool_mcp("create_decision", arguments))
        return results

    results = asyncio.run(scenario())

    for result in results:
        assert result.is_error is True
        assert result.structured_content["error_code"] == "approval_mismatch"
        assert result.structured_content["data"] is None
    assert _decisions(gateway) == 0
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 0
    assert [event.detail["error_code"] for event in _denied(gateway)] == [
        "approval_mismatch",
        "approval_mismatch",
        "approval_mismatch",
    ]
    assert gateway.store.get_idempotency(TENANT, PRINCIPAL, "create_decision", "idem-case") is None


def test_valid_approval_commits_one_decision_crm_stage_and_audit_chain(gateway) -> None:
    result = asyncio.run(
        _call(
            gateway,
            _approver_token(gateway),
            "create_decision",
            _decision_call(approval_token=_approval(gateway)),
        )
    )

    assert result.is_error is False
    data = result.structured_content["data"]
    assert data["case_id"] == CASE_ID
    assert data["decision"] == "approve"
    assert data["approver"] == APPROVER
    assert data["principal"] == PRINCIPAL
    assert data["replayed"] is False
    assert data["decision_id"].startswith("dec_")

    assert _decisions(gateway) == 1
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 1
    case = gateway.store.get_case(TENANT, CASE_ID)
    stage = gateway.store.latest_crm_stage(TENANT, CASE_ID)
    assert case is not None and case.status == "approved"
    assert stage is not None
    assert stage.stage == "approved"
    assert stage.decision_id == data["decision_id"]
    assert gateway.store.get_idempotency(TENANT, PRINCIPAL, "create_decision", "idem-1") is not None

    committed = [
        event
        for event in gateway.store.list_audit(TENANT, CASE_ID)
        if event.event == "write_committed"
    ]
    assert len(committed) == 1
    assert committed[0].principal == PRINCIPAL
    assert committed[0].output_hash
    assert committed[0].detail["decision_id"] == data["decision_id"]
    assert committed[0].detail["approver"] == APPROVER


def test_same_key_and_args_replays_the_stored_result_without_a_second_decision(gateway) -> None:
    token = _approver_token(gateway)
    approval = _approval(gateway)

    async def scenario():
        async with _client(gateway, token) as client:
            first = await client.call_tool_mcp(
                "create_decision", _decision_call(approval_token=approval)
            )
            retry = await client.call_tool_mcp(
                "create_decision", _decision_call(approval_token=approval)
            )
            return first, retry

    first, retry = asyncio.run(scenario())

    assert first.is_error is False
    assert retry.is_error is False
    assert first.structured_content["data"]["replayed"] is False
    assert retry.structured_content["data"]["replayed"] is True
    assert (
        retry.structured_content["data"]["decision_id"]
        == (first.structured_content["data"]["decision_id"])
    )
    assert _decisions(gateway) == 1
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 1

    replays = [
        event
        for event in gateway.store.list_audit(TENANT, CASE_ID)
        if event.event == "idempotent_replay"
    ]
    assert len(replays) == 1
    assert replays[0].detail["decision_id"] == first.structured_content["data"]["decision_id"]


def test_same_key_with_changed_args_returns_idempotency_conflict(gateway) -> None:
    token = _approver_token(gateway)
    approval = _approval(gateway)
    changed = _approval(gateway, rationale="Changed rationale.")

    async def scenario():
        async with _client(gateway, token) as client:
            await client.call_tool_mcp("create_decision", _decision_call(approval_token=approval))
            return await client.call_tool_mcp(
                "create_decision",
                _decision_call(
                    approval_token=changed,
                    rationale="Changed rationale.",
                ),
            )

    conflict = asyncio.run(scenario())

    assert conflict.is_error is True
    assert conflict.structured_content["error_code"] == "idempotency_conflict"
    assert conflict.structured_content["data"] is None
    assert _decisions(gateway) == 1
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 1
    conflicts = [
        event
        for event in gateway.store.list_audit(TENANT, CASE_ID)
        if event.event == "idempotency_conflict"
    ]
    assert len(conflicts) == 1
    assert conflicts[0].detail["idempotency_key"] == "idem-1"


def test_a_denied_approval_can_be_retried_with_a_fresh_token(gateway) -> None:
    token = _approver_token(gateway)

    async def scenario():
        async with _client(gateway, token) as client:
            denied = await client.call_tool_mcp(
                "create_decision",
                _decision_call(approval_token=_approval(gateway, ttl_seconds=-5)),
            )
            committed = await client.call_tool_mcp(
                "create_decision",
                _decision_call(approval_token=_approval(gateway, jti="jti-fresh")),
            )
            return denied, committed

    denied, committed = asyncio.run(scenario())

    assert denied.is_error is True
    assert denied.structured_content["error_code"] == "approval_expired"
    assert committed.is_error is False
    assert committed.structured_content["data"]["replayed"] is False
    assert _decisions(gateway) == 1
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 1


def test_a_used_approval_token_is_rejected_for_a_new_idempotency_key(gateway) -> None:
    token = _approver_token(gateway)
    approval = _approval(gateway, jti="jti-once")

    async def scenario():
        async with _client(gateway, token) as client:
            first = await client.call_tool_mcp(
                "create_decision", _decision_call(approval_token=approval, idempotency_key="idem-1")
            )
            reused = await client.call_tool_mcp(
                "create_decision", _decision_call(approval_token=approval, idempotency_key="idem-2")
            )
            return first, reused

    first, reused = asyncio.run(scenario())

    assert first.is_error is False
    assert reused.is_error is True
    assert reused.structured_content["error_code"] == "approval_reused"
    assert _decisions(gateway) == 1
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 1
    assert gateway.store.get_idempotency(TENANT, PRINCIPAL, "create_decision", "idem-2") is None
    assert _denied(gateway)[-1].detail["error_code"] == "approval_reused"


def test_approval_for_another_tenant_is_refused(gateway) -> None:
    result = asyncio.run(
        _call(
            gateway,
            _approver_token(gateway),
            "create_decision",
            _decision_call(approval_token=_approval(gateway, tenant_id="tenant_other")),
        )
    )

    assert result.is_error is True
    assert result.structured_content["error_code"] == "approval_mismatch"
    assert _decisions(gateway) == 0


def test_get_audit_shows_the_commit_replay_and_conflict_chain(gateway) -> None:
    token = _approver_token(gateway)
    approval = _approval(gateway)

    async def scenario():
        async with _client(gateway, token) as client:
            committed = await client.call_tool_mcp(
                "create_decision", _decision_call(approval_token=approval)
            )
            await client.call_tool_mcp("create_decision", _decision_call(approval_token=approval))
            await client.call_tool_mcp(
                "create_decision",
                _decision_call(
                    approval_token=_approval(gateway, rationale="Changed rationale."),
                    rationale="Changed rationale.",
                ),
            )
            audit = await client.call_tool_mcp("get_audit", {"case_id": CASE_ID, "limit": 50})
            return committed, audit

    committed, audit = asyncio.run(scenario())

    events = audit.structured_content["data"]["events"]
    decision_events = [event["event"] for event in events if event["tool"] == "create_decision"]
    assert decision_events == ["write_committed", "idempotent_replay", "idempotency_conflict"]
    committed_event = next(event for event in events if event["event"] == "write_committed")
    assert (
        committed_event["detail"]["decision_id"]
        == (committed.structured_content["data"]["decision_id"])
    )


def test_write_demo_shows_every_path_and_exits_zero(gateway, capsys) -> None:
    exit_code = asyncio.run(
        run_write_demo(
            url=gateway.url,
            case_id=CASE_ID,
            agent_token=_approver_token(gateway),
            approval=gateway.approval,
            tenant_id=TENANT,
        )
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    for expected in (
        "approval_required",
        "committed",
        "replayed=true",
        "idempotency_conflict",
        "approval_expired",
        "approval_mismatch",
        "approval_reused",
        "write_committed",
        "idempotent_replay",
    ):
        assert expected in output
    assert _decisions(gateway) == 1
    assert gateway.store.count_crm_stages(TENANT, CASE_ID) == 1
