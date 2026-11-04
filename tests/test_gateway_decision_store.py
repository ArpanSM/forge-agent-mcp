from __future__ import annotations

from pathlib import Path

import pytest

from forge_agent_mcp.corpus.manifest import load_manifest
from forge_agent_mcp.gateway.approval import decision_payload_hash
from forge_agent_mcp.gateway.store import (
    ApprovalReusedError,
    GatewayStore,
    IdempotencyConflictError,
)
from tests.conftest import MANIFEST_PATH, OTHER_TENANT, TENANT

CASE_ID = "case_onb_ridgeline"
PRINCIPAL = "agent-underwriter"
APPROVER = "underwriter@example.test"


@pytest.fixture
def store(tmp_path: Path) -> GatewayStore:
    gateway_store = GatewayStore(tmp_path / "gateway.db")
    gateway_store.initialize()
    gateway_store.seed_demo(load_manifest(MANIFEST_PATH), tenant_id=TENANT)
    return gateway_store


def _payload_hash(rationale: str = "Clean packet.") -> str:
    return decision_payload_hash(
        case_id=CASE_ID,
        decision="approve",
        rationale=rationale,
        conditions=[],
    )


def _commit(
    store: GatewayStore,
    *,
    idempotency_key: str = "idem-1",
    jti: str = "jti-1",
    rationale: str = "Clean packet.",
    decision: str = "approve",
    tenant_id: str = TENANT,
):
    return store.commit_decision(
        tenant_id=tenant_id,
        case_id=CASE_ID,
        decision=decision,
        rationale=rationale,
        conditions=[],
        principal=PRINCIPAL,
        approver=APPROVER,
        approval_jti=jti,
        approval_expires_at="2026-11-05T12:00:00+00:00",
        payload_hash=_payload_hash(rationale),
        idempotency_key=idempotency_key,
        input_hash="a" * 64,
    )


def test_commit_decision_writes_one_row_in_every_table(store: GatewayStore) -> None:
    result = _commit(store)

    assert result.decision == "approve"
    assert result.approver == APPROVER
    assert result.principal == PRINCIPAL
    assert result.replayed is False
    assert store.count_decisions(TENANT, CASE_ID) == 1
    assert store.latest_decision(TENANT, CASE_ID) == result

    stage = store.latest_crm_stage(TENANT, CASE_ID)
    assert stage is not None
    assert stage.stage == "approved"
    assert stage.decision_id == result.decision_id
    assert store.count_crm_stages(TENANT, CASE_ID) == 1

    case = store.get_case(TENANT, CASE_ID)
    assert case is not None
    assert case.status == "approved"

    entry = store.get_idempotency(TENANT, PRINCIPAL, "create_decision", "idem-1")
    assert entry is not None
    assert entry.args_hash == _payload_hash()
    assert entry.result["decision_id"] == result.decision_id
    assert store.get_idempotency(OTHER_TENANT, PRINCIPAL, "create_decision", "idem-1") is None
    assert store.approval_is_used(TENANT, "jti-1") is True
    assert store.approval_is_used(OTHER_TENANT, "jti-1") is False

    committed = [
        event for event in store.list_audit(TENANT, CASE_ID) if event.event == "write_committed"
    ]
    assert len(committed) == 1
    assert committed[0].outcome == "ok"
    assert committed[0].principal == PRINCIPAL
    assert committed[0].output_hash
    assert committed[0].detail["decision_id"] == result.decision_id
    assert committed[0].detail["approver"] == APPROVER
    assert committed[0].detail["jti"] == "jti-1"
    assert committed[0].detail["idempotency_key"] == "idem-1"


def test_commit_decision_writes_no_decision_when_the_jti_was_used(store: GatewayStore) -> None:
    first = _commit(store, idempotency_key="idem-1", jti="jti-once")

    with pytest.raises(ApprovalReusedError):
        _commit(store, idempotency_key="idem-2", jti="jti-once", rationale="A second write.")

    assert store.count_decisions(TENANT, CASE_ID) == 1
    assert store.count_crm_stages(TENANT, CASE_ID) == 1
    assert store.latest_decision(TENANT, CASE_ID) == first
    assert store.get_idempotency(TENANT, PRINCIPAL, "create_decision", "idem-2") is None


def test_commit_decision_rejects_a_conflicting_key_without_writing(store: GatewayStore) -> None:
    first = _commit(store, idempotency_key="idem-1", jti="jti-1")

    with pytest.raises(IdempotencyConflictError):
        _commit(store, idempotency_key="idem-1", jti="jti-2", rationale="Changed arguments.")

    assert store.count_decisions(TENANT, CASE_ID) == 1
    assert store.count_crm_stages(TENANT, CASE_ID) == 1
    assert store.latest_decision(TENANT, CASE_ID) == first
    assert store.approval_is_used(TENANT, "jti-2") is False


@pytest.mark.parametrize(
    ("decision", "case_status", "crm_stage"),
    [
        ("conditional_approve", "approved", "conditionally_approved"),
        ("decline", "declined", "declined"),
        ("request_documents", "awaiting_documents", "awaiting_documents"),
    ],
)
def test_commit_decision_maps_each_decision_to_a_case_status_and_crm_stage(
    store: GatewayStore, decision: str, case_status: str, crm_stage: str
) -> None:
    _commit(store, decision=decision)

    case = store.get_case(TENANT, CASE_ID)
    stage = store.latest_crm_stage(TENANT, CASE_ID)

    assert case is not None
    assert case.status == case_status
    assert stage is not None
    assert stage.stage == crm_stage
