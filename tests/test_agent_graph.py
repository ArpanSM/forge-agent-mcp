from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from forge_agent_mcp.agent.graph import CaseRunner
from forge_agent_mcp.agent.models import ApprovalAction
from tests.fake_gateway import APPROVER, TENANT, FakeGateway

ONBOARDING_CASE = "case_onb_ridgeline"
ONBOARDING_HARD_STOP = "case_onb_cedar_pine"
INVOICE_HIGH = "case_inv_northgate"
INVOICE_MISSING_DOCUMENT = "case_inv_sagebrush"
ONBOARDING_MEDIUM = "case_onb_quarry_road"


def _runner(
    generated_corpus: tuple,
    tmp_path: Path,
    *,
    case_ids: list[str] | None = None,
    checkpoint_name: str = "checkpoints.db",
) -> tuple[CaseRunner, FakeGateway]:
    manifest, corpus = generated_corpus
    fake = FakeGateway.from_corpus(
        manifest,
        corpus,
        case_ids=case_ids
        or [
            ONBOARDING_CASE,
            ONBOARDING_HARD_STOP,
            INVOICE_HIGH,
            INVOICE_MISSING_DOCUMENT,
            ONBOARDING_MEDIUM,
        ],
        tenant_id=TENANT,
    )
    runner = CaseRunner(
        fake,
        corpus_dir=corpus.root,
        checkpoint_path=tmp_path / checkpoint_name,
    )
    return runner, fake


def _action(
    action: str,
    *,
    rationale: str,
    conditions: list[str] | None = None,
    note: str = "",
) -> ApprovalAction:
    return ApprovalAction(
        action=action,  # type: ignore[arg-type]
        approver=APPROVER,
        rationale=rationale,
        conditions=conditions or [],
        note=note,
        approval_token=f"approval-token-{uuid.uuid4().hex}",
        approval_jwt=f"approval-jwt-{uuid.uuid4().hex}",
    )


def test_clean_onboarding_case_runs_to_pending_approval(generated_corpus, tmp_path) -> None:
    runner, fake = _runner(generated_corpus, tmp_path)

    result = runner.start(ONBOARDING_CASE)

    assert result.status == "pending_approval"
    assert result.stage == "summarise"
    assert result.transitions == ["intake", "extract", "validate", "cross_check", "summarise"]
    assert result.case is not None
    assert result.case.case_type == "onboarding"
    assert result.extraction is not None
    assert result.extraction.status == "extracted"
    assert len(result.extraction.documents) == 5
    assert result.flags == []
    assert result.recommendation is not None
    assert result.recommendation.recommendation == "approve"
    assert result.recommendation.decision == "approve"
    assert result.approval_request is not None
    assert result.approval_request.case_id == ONBOARDING_CASE
    assert result.approval_request.recommendation.decision == "approve"
    assert result.decision is None
    assert result.audit_verified is False

    assert len(fake.extraction_calls) == 5
    assert len(fake.recommendation_calls) == 1
    assert fake.decision_calls == []
    assert fake.read_case(ONBOARDING_CASE).extraction_count == 5
    assert fake.read_case(ONBOARDING_CASE).recommendation is not None


def test_invoice_high_case_recommends_request_documents(generated_corpus, tmp_path) -> None:
    runner, _fake = _runner(generated_corpus, tmp_path)

    result = runner.start(INVOICE_HIGH)

    assert result.status == "pending_approval"
    assert result.recommendation is not None
    assert result.recommendation.recommendation == "hold"
    assert result.recommendation.decision == "request_documents"
    assert result.approval_request is not None
    assert result.approval_request.recommendation.decision == "request_documents"
    assert {flag.code for flag in result.flags} & {
        "invoice_arithmetic_mismatch",
        "pod_mismatch",
    }


def test_medium_onboarding_case_recommends_conditional_approval(generated_corpus, tmp_path) -> None:
    runner, _fake = _runner(generated_corpus, tmp_path)

    result = runner.start(ONBOARDING_MEDIUM)

    assert result.status == "pending_approval"
    assert result.recommendation is not None
    assert result.recommendation.recommendation == "conditional_approve"
    assert result.recommendation.conditions
    assert any(flag.code == "authority_young" for flag in result.flags)


def test_resume_with_approval_writes_once_and_verifies_the_audit_chain(
    generated_corpus, tmp_path
) -> None:
    runner, fake = _runner(generated_corpus, tmp_path)
    pending = runner.start(ONBOARDING_CASE)

    action = _action(
        "approve",
        rationale="Clean packet, identity and insurance verified.",
        note="Reviewed every citation.",
    )
    result = runner.resume(ONBOARDING_CASE, action)

    assert result.status == "approved"
    assert result.stage == "audit_close"
    assert result.transitions == [
        "intake",
        "extract",
        "validate",
        "cross_check",
        "summarise",
        "human_approval",
        "execute",
        "audit_close",
    ]
    assert result.decision is not None
    assert result.decision.decision == "approve"
    assert result.decision.approver == APPROVER
    assert result.audit_verified is True
    assert any(event.event == "write_committed" for event in result.audit)
    assert len(fake.decision_calls) == 1
    call = fake.decision_calls[0]
    assert call["approval_token"] == action.approval_token
    assert call["approval_jwt"] == action.approval_jwt
    assert call["approval_token"] != ""
    assert call["idempotency_key"].startswith("idem_")
    assert fake.read_case(ONBOARDING_CASE).status == "approved"
    assert pending.status == "pending_approval"


def test_resume_with_decline_records_a_rejection(generated_corpus, tmp_path) -> None:
    runner, fake = _runner(generated_corpus, tmp_path)
    runner.start(ONBOARDING_HARD_STOP)

    result = runner.resume(
        ONBOARDING_HARD_STOP,
        _action("decline", rationale="Insurance expired before the as-of date."),
    )

    assert result.status == "declined"
    assert result.decision is not None
    assert result.decision.decision == "decline"
    assert fake.read_case(ONBOARDING_HARD_STOP).status == "declined"
    assert fake.read_case(ONBOARDING_HARD_STOP).crm_stage is not None
    assert fake.read_case(ONBOARDING_HARD_STOP).crm_stage.stage == "declined"
    assert len(fake.decision_calls) == 1


def test_resume_with_request_documents_sets_awaiting_documents(generated_corpus, tmp_path) -> None:
    runner, fake = _runner(generated_corpus, tmp_path)
    runner.start(INVOICE_MISSING_DOCUMENT)

    result = runner.resume(
        INVOICE_MISSING_DOCUMENT,
        _action(
            "request_documents",
            rationale="The proof of delivery is missing.",
            conditions=["Obtain a signed proof of delivery."],
        ),
    )

    assert result.status == "awaiting_documents"
    assert result.decision is not None
    assert result.decision.decision == "request_documents"
    assert result.decision.conditions == ["Obtain a signed proof of delivery."]
    case = fake.read_case(INVOICE_MISSING_DOCUMENT)
    assert case.status == "awaiting_documents"
    assert case.crm_stage is not None
    assert case.crm_stage.stage == "awaiting_documents"


def test_pending_approval_survives_a_runner_restart(generated_corpus, tmp_path) -> None:
    first, fake = _runner(generated_corpus, tmp_path)
    first.start(ONBOARDING_CASE)
    first.close()

    second = CaseRunner(
        fake,
        corpus_dir=generated_corpus[1].root,
        checkpoint_path=tmp_path / "checkpoints.db",
    )
    recovered = second.get(ONBOARDING_CASE)

    assert recovered is not None
    assert recovered.status == "pending_approval"
    assert recovered.approval_request is not None
    assert recovered.approval_request.case_id == ONBOARDING_CASE
    assert len(fake.decision_calls) == 0

    result = second.resume(
        ONBOARDING_CASE,
        _action("approve", rationale="Approved after restart."),
    )

    assert result.status == "approved"
    assert result.decision is not None
    assert result.decision.decision == "approve"
    assert len(fake.decision_calls) == 1


def test_resuming_a_completed_case_does_not_write_again(generated_corpus, tmp_path) -> None:
    runner, fake = _runner(generated_corpus, tmp_path)
    runner.start(ONBOARDING_CASE)
    first = runner.resume(ONBOARDING_CASE, _action("approve", rationale="Approved once."))

    second = runner.resume(ONBOARDING_CASE, _action("approve", rationale="Approved once."))

    assert second.status == "approved"
    assert second.decision is not None
    assert second.decision.decision_id == first.decision.decision_id
    assert len(fake.decision_calls) == 1


def test_idempotency_key_is_deterministic_for_the_same_case_and_decision(
    generated_corpus, tmp_path
) -> None:
    first_runner, first_fake = _runner(generated_corpus, tmp_path, checkpoint_name="first.db")
    second_runner, second_fake = _runner(generated_corpus, tmp_path, checkpoint_name="second.db")

    first_runner.start(ONBOARDING_CASE)
    first_runner.resume(ONBOARDING_CASE, _action("approve", rationale="Same payload."))
    second_runner.start(ONBOARDING_CASE)
    second_runner.resume(ONBOARDING_CASE, _action("approve", rationale="Same payload."))

    first_key = first_fake.decision_calls[0]["idempotency_key"]
    second_key = second_fake.decision_calls[0]["idempotency_key"]
    assert first_key == second_key


def test_unknown_case_returns_a_failed_result(generated_corpus, tmp_path) -> None:
    runner, _fake = _runner(generated_corpus, tmp_path)

    result = runner.start("case_missing")

    assert result.status == "failed"
    assert result.error is not None
    assert "case_missing" in result.error
    assert result.decision is None


def test_resume_without_a_pending_interrupt_is_refused(generated_corpus, tmp_path) -> None:
    runner, _fake = _runner(generated_corpus, tmp_path)

    with pytest.raises(Exception, match="not been started|not waiting"):
        runner.resume(ONBOARDING_CASE, _action("approve", rationale="Too early."))
