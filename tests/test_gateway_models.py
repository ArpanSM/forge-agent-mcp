from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge_agent_mcp.gateway.models import (
    ApprovalRecord,
    CitationView,
    DecisionRecord,
    FlagRecord,
    IdempotencyRecord,
)


def test_approval_and_idempotency_models_are_typed() -> None:
    approval = ApprovalRecord(
        tenant_id="tenant_northwind",
        approval_id="apr_1",
        case_id="case_onb_ridgeline",
        jti="jti_1",
        approver="underwriter@example.test",
        payload_hash="a" * 64,
        expires_at="2026-11-05T12:00:00Z",
        created_at="2026-11-05T11:00:00Z",
    )
    decision = DecisionRecord(
        decision_id="dec_1",
        case_id="case_onb_ridgeline",
        decision="approve",
        rationale="Clean packet.",
        conditions=[],
        approver="underwriter@example.test",
        principal="agent-underwriter",
        created_at="2026-11-05T11:00:00Z",
    )
    entry = IdempotencyRecord(
        tenant_id="tenant_northwind",
        principal="agent-underwriter",
        tool="create_decision",
        key="idem-1",
        args_hash="b" * 64,
        result=decision,
        created_at="2026-11-05T11:00:00Z",
    )

    assert approval.used_at is None
    assert entry.result.decision_id == "dec_1"


def test_approval_model_rejects_a_malformed_payload_hash() -> None:
    with pytest.raises(ValidationError):
        ApprovalRecord(
            tenant_id="tenant_northwind",
            approval_id="apr_1",
            case_id="case_onb_ridgeline",
            jti="jti_1",
            approver="underwriter@example.test",
            payload_hash="too-short",
            expires_at="2026-11-05T12:00:00Z",
            created_at="2026-11-05T11:00:00Z",
        )


def test_citation_view_rejects_an_empty_locator() -> None:
    with pytest.raises(ValidationError, match="document_id or record_path"):
        CitationView()


def test_flag_evidence_rejects_an_all_null_citation() -> None:
    with pytest.raises(ValidationError, match="document_id or record_path"):
        FlagRecord(
            code="authority_young",
            severity="medium",
            rule="test",
            details="test",
            evidence={},
        )
