from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from forge_agent_mcp.corpus.manifest import load_manifest
from forge_agent_mcp.corpus.vocab import Decision
from forge_agent_mcp.gateway.approval import (
    CONSOLE_ISSUER,
    ApprovalClaims,
    decision_payload_hash,
)
from forge_agent_mcp.gateway.canonical import canonical_hash
from forge_agent_mcp.gateway.models import CreateDecisionInput
from forge_agent_mcp.gateway.store import (
    ApprovalReusedError,
    CaseAlreadyDecidedError,
    GatewayStore,
    IdempotencyConflictError,
    UnknownDecisionError,
    decision_outcome,
)
from tests.conftest import MANIFEST_PATH, OTHER_TENANT, TENANT

CASE_ID = "case_onb_ridgeline"
PRINCIPAL = "agent-underwriter"
APPROVER = "underwriter@example.test"
AUDIENCE = "forge-freight-gateway"


@pytest.fixture
def store(tmp_path: Path) -> GatewayStore:
    gateway_store = GatewayStore(tmp_path / "gateway.db")
    gateway_store.initialize()
    gateway_store.seed_demo(load_manifest(MANIFEST_PATH), tenant_id=TENANT)
    return gateway_store


def _request(
    *,
    idempotency_key: str = "idem-1",
    decision: Decision = "approve",
    rationale: str = "Clean packet.",
) -> CreateDecisionInput:
    return CreateDecisionInput(
        case_id=CASE_ID,
        decision=decision,
        rationale=rationale,
        conditions=[],
        approval_token="opaque-token",
        idempotency_key=idempotency_key,
    )


def _claims(request: CreateDecisionInput, *, jti: str = "jti-1") -> ApprovalClaims:
    now = int(time.time())
    return ApprovalClaims(
        iss=CONSOLE_ISSUER,
        aud=AUDIENCE,
        sub=APPROVER,
        tid=TENANT,
        case_id=request.case_id,
        decision=request.decision,
        payload_hash=decision_payload_hash(
            case_id=request.case_id,
            decision=request.decision,
            rationale=request.rationale,
            conditions=request.conditions,
        ),
        exp=now + 600,
        iat=now,
        jti=jti,
    )


def _commit(
    store: GatewayStore,
    *,
    idempotency_key: str = "idem-1",
    jti: str = "jti-1",
    rationale: str = "Clean packet.",
    decision: Decision = "approve",
    request: CreateDecisionInput | None = None,
):
    request = request or _request(
        idempotency_key=idempotency_key,
        decision=decision,
        rationale=rationale,
    )
    return store.commit_decision(
        tenant_id=TENANT,
        request=request,
        claims=_claims(request, jti=jti),
        principal=PRINCIPAL,
        input_hash=canonical_hash(request),
    )


def _events(store: GatewayStore, event: str):
    return [item for item in store.list_audit(TENANT, CASE_ID) if item.event == event]


def test_commit_decision_writes_one_row_in_every_table(store: GatewayStore) -> None:
    request = _request()
    result = _commit(store, request=request)

    assert result.decision == "approve"
    assert result.approver == APPROVER
    assert result.principal == PRINCIPAL
    assert result.replayed is False
    assert store.latest_decision(TENANT, CASE_ID) == result

    stage = store.latest_crm_stage(TENANT, CASE_ID)
    assert stage is not None
    assert stage.stage == "approved"
    assert stage.decision_id == result.decision_id

    case = store.get_case(TENANT, CASE_ID)
    assert case is not None
    assert case.status == "approved"

    entry = store.get_idempotency(TENANT, PRINCIPAL, "create_decision", "idem-1")
    assert entry is not None
    assert entry.args_hash == _claims(request).payload_hash
    assert entry.result.decision_id == result.decision_id
    assert entry.result.decision == "approve"
    assert store.get_idempotency(OTHER_TENANT, PRINCIPAL, "create_decision", "idem-1") is None
    assert store.approval_is_used(TENANT, "jti-1") is True
    assert store.approval_is_used(OTHER_TENANT, "jti-1") is False

    committed = _events(store, "write_committed")
    assert len(committed) == 1
    assert committed[0].outcome == "ok"
    assert committed[0].principal == PRINCIPAL
    assert committed[0].output_hash
    assert committed[0].detail["decision_id"] == result.decision_id
    assert committed[0].detail["approver"] == APPROVER
    assert committed[0].detail["jti"] == "jti-1"
    assert committed[0].detail["idempotency_key"] == "idem-1"


def test_commit_decision_replays_in_transaction_and_writes_one_replay_audit(
    store: GatewayStore,
) -> None:
    request = _request()
    first = _commit(store, request=request)
    replay = _commit(store, request=request, jti="jti-not-consumed")

    assert replay.replayed is True
    assert replay.decision_id == first.decision_id
    assert len(_events(store, "write_committed")) == 1
    replays = _events(store, "idempotent_replay")
    assert len(replays) == 1
    assert replays[0].outcome == "ok"
    assert replays[0].detail["decision_id"] == first.decision_id
    assert replays[0].detail["idempotency_key"] == "idem-1"


def test_commit_decision_writes_no_decision_when_the_jti_was_used(store: GatewayStore) -> None:
    first = _commit(store, idempotency_key="idem-1", jti="jti-once")

    with pytest.raises(ApprovalReusedError):
        _commit(store, idempotency_key="idem-2", jti="jti-once", rationale="A second write.")

    assert store.latest_decision(TENANT, CASE_ID) == first
    assert store.get_idempotency(TENANT, PRINCIPAL, "create_decision", "idem-2") is None
    assert len(_events(store, "write_committed")) == 1


def test_commit_decision_rejects_a_conflicting_key_without_writing(store: GatewayStore) -> None:
    first = _commit(store, idempotency_key="idem-1", jti="jti-1")

    with pytest.raises(IdempotencyConflictError):
        _commit(store, idempotency_key="idem-1", jti="jti-2", rationale="Changed arguments.")

    assert store.latest_decision(TENANT, CASE_ID) == first
    assert store.approval_is_used(TENANT, "jti-2") is False
    assert len(_events(store, "write_committed")) == 1


@pytest.mark.parametrize(
    ("decision", "case_status", "crm_stage"),
    [
        ("approve", "approved", "approved"),
        ("conditional_approve", "approved", "conditionally_approved"),
        ("decline", "declined", "declined"),
        ("request_documents", "awaiting_documents", "awaiting_documents"),
    ],
)
def test_commit_decision_maps_each_decision_to_a_case_status_and_crm_stage(
    store: GatewayStore, decision: Decision, case_status: str, crm_stage: str
) -> None:
    _commit(store, decision=decision)

    case = store.get_case(TENANT, CASE_ID)
    stage = store.latest_crm_stage(TENANT, CASE_ID)

    assert case is not None
    assert case.status == case_status
    assert stage is not None
    assert stage.stage == crm_stage


def test_unknown_decision_raises_a_domain_error() -> None:
    with pytest.raises(UnknownDecisionError):
        decision_outcome(cast(Decision, "not_a_decision"))


def test_commit_decision_refuses_a_second_decision_for_the_same_case(store: GatewayStore) -> None:
    first = _commit(store, idempotency_key="idem-1", jti="jti-1")

    with pytest.raises(CaseAlreadyDecidedError) as excinfo:
        _commit(store, idempotency_key="idem-2", jti="jti-2", rationale="A second decision.")

    assert excinfo.value.decision.decision_id == first.decision_id
    assert store.latest_decision(TENANT, CASE_ID) == first
    assert len(_events(store, "write_committed")) == 1


def test_concurrent_commits_allow_exactly_one_decision(store: GatewayStore) -> None:
    def submit(index: int):
        try:
            return _commit(
                store,
                idempotency_key=f"idem-concurrent-{index}",
                jti=f"jti-concurrent-{index}",
                rationale=f"Concurrent review {index}.",
            )
        except CaseAlreadyDecidedError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [1, 2]))

    decisions = [result for result in results if not isinstance(result, CaseAlreadyDecidedError)]
    refusals = [result for result in results if isinstance(result, CaseAlreadyDecidedError)]
    assert len(decisions) == 1
    assert len(refusals) == 1
    assert store.latest_decision(TENANT, CASE_ID) is not None
    assert len(_events(store, "write_committed")) == 1
