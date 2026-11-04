from __future__ import annotations

from pathlib import Path

from forge_agent_mcp.agent.gateway import FastMCPGatewayClient
from forge_agent_mcp.agent.graph import CaseRunner
from forge_agent_mcp.agent.models import ApprovalAction
from forge_agent_mcp.gateway.auth import APPROVE_SCOPE, READ_SCOPE, WRITE_SCOPE
from tests.conftest import APPROVER, TENANT


def _runner(gateway, tmp_path: Path, corpus_root: Path) -> CaseRunner:
    token = gateway.token(
        subject="agent-underwriter",
        scopes=[READ_SCOPE, WRITE_SCOPE],
    )
    return CaseRunner(
        FastMCPGatewayClient(gateway.url, token),
        corpus_dir=corpus_root,
        checkpoint_path=tmp_path / "checkpoints.db",
    )


def _approval(gateway, *, case_id: str, decision: str, rationale: str):
    token = gateway.approval.issue_for_decision(
        approver=APPROVER,
        tenant_id=TENANT,
        case_id=case_id,
        decision=decision,  # type: ignore[arg-type]
        rationale=rationale,
        conditions=[],
    )
    jwt = gateway.issuer.issue(
        subject=APPROVER,
        tenant_id=TENANT,
        scopes=[READ_SCOPE, APPROVE_SCOPE],
        ttl_seconds=300,
    )
    return ApprovalAction(
        action=decision,  # type: ignore[arg-type]
        approver=APPROVER,
        rationale=rationale,
        conditions=[],
        approval_token=token,
        approval_jwt=jwt,
    )


def test_agent_runs_a_clean_case_over_real_mcp_http(gateway, generated_corpus, tmp_path) -> None:
    _manifest, corpus = generated_corpus
    runner = _runner(gateway, tmp_path, corpus.root)

    pending = runner.start("case_onb_ridgeline")

    assert pending.status == "pending_approval"
    assert pending.recommendation is not None
    assert pending.recommendation.decision == "approve"
    assert pending.case is not None
    assert pending.case.extraction_count == 5

    result = runner.resume(
        "case_onb_ridgeline",
        _approval(
            gateway,
            case_id="case_onb_ridgeline",
            decision="approve",
            rationale="Clean packet approved over the real gateway.",
        ),
    )

    assert result.status == "approved"
    assert result.decision is not None
    assert result.decision.decision == "approve"
    assert result.audit_verified is True
    case = gateway.store.get_case(TENANT, "case_onb_ridgeline")
    assert case is not None and case.status == "approved"
    assert gateway.store.latest_crm_stage(TENANT, "case_onb_ridgeline") is not None
    assert (
        len(
            [
                event
                for event in gateway.store.list_audit(TENANT, "case_onb_ridgeline")
                if event.event == "write_committed"
            ]
        )
        == 1
    )


def test_agent_runs_an_invoice_request_documents_case_over_real_mcp_http(
    gateway, generated_corpus, tmp_path
) -> None:
    _manifest, corpus = generated_corpus
    runner = _runner(gateway, tmp_path, corpus.root)

    pending = runner.start("case_inv_northgate")

    assert pending.status == "pending_approval"
    assert pending.recommendation is not None
    assert pending.recommendation.decision == "request_documents"

    result = runner.resume(
        "case_inv_northgate",
        _approval(
            gateway,
            case_id="case_inv_northgate",
            decision="request_documents",
            rationale="Hold the invoice until the arithmetic and POD are reconciled.",
        ),
    )

    assert result.status == "awaiting_documents"
    assert result.decision is not None
    assert result.decision.decision == "request_documents"
    assert result.audit_verified is True
    case = gateway.store.get_case(TENANT, "case_inv_northgate")
    assert case is not None and case.status == "awaiting_documents"
    stage = gateway.store.latest_crm_stage(TENANT, "case_inv_northgate")
    assert stage is not None and stage.stage == "awaiting_documents"


def test_every_corpus_archetype_reaches_pending_approval_over_real_mcp_http(
    gateway, generated_corpus, tmp_path
) -> None:
    _manifest, corpus = generated_corpus
    runner = _runner(gateway, tmp_path, corpus.root)

    failures: list[tuple[str, str, str | None]] = []
    for case in corpus.index.cases:
        result = runner.start(case.case_id)
        if result.status != "pending_approval":
            failures.append((case.case_id, result.status, result.error))

    assert failures == []
    assert len(corpus.index.cases) == 16
