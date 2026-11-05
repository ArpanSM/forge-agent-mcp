from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

from forge_agent_mcp.agent.gateway import DecisionCommand
from forge_agent_mcp.agent.graph import CaseRunner
from forge_agent_mcp.console.app import create_console_app
from forge_agent_mcp.console.models import CaseCatalogEntry
from forge_agent_mcp.console.service import ConsoleService
from forge_agent_mcp.gateway.approval import CONSOLE_ISSUER, ApprovalTokenIssuer
from forge_agent_mcp.gateway.auth import LocalTokenIssuer
from tests.fake_gateway import APPROVER, TENANT, FakeGateway

ONBOARDING_CASE = "case_onb_ridgeline"
ONBOARDING_HARD_STOP = "case_onb_cedar_pine"
ONBOARDING_MEDIUM = "case_onb_quarry_road"
INVOICE_HIGH = "case_inv_northgate"
INVOICE_MISSING_DOCUMENT = "case_inv_sagebrush"

APPROVAL_SECRET = "console-approval-test-secret-at-least-32-bytes"
JWT_SECRET = "console-jwt-test-secret-at-least-32-bytes"
ISSUER = "http://127.0.0.1:8765"
AUDIENCE = "forge-freight-gateway"

CASES = [
    ONBOARDING_CASE,
    ONBOARDING_HARD_STOP,
    ONBOARDING_MEDIUM,
    INVOICE_HIGH,
    INVOICE_MISSING_DOCUMENT,
]


def _catalog(generated_corpus) -> list[CaseCatalogEntry]:
    manifest, corpus = generated_corpus
    entries: list[CaseCatalogEntry] = []
    for case in corpus.index.cases:
        if case.case_id not in CASES:
            continue
        carrier = manifest.carrier_by_id[case.carrier_id]
        entries.append(
            CaseCatalogEntry(
                case_id=case.case_id,
                case_type=case.case_type,
                carrier_name=carrier.legal_name,
            )
        )
    return entries


def _console(generated_corpus, tmp_path: Path) -> tuple[TestClient, FakeGateway, ConsoleService]:
    manifest, corpus = generated_corpus
    fake = FakeGateway.from_corpus(manifest, corpus, case_ids=CASES, tenant_id=TENANT)
    runner = CaseRunner(
        fake,
        corpus_dir=corpus.root,
        checkpoint_path=tmp_path / "checkpoints.db",
    )
    service = ConsoleService(
        runner=runner,
        gateway=fake,
        catalog=_catalog(generated_corpus),
        approval_issuer=ApprovalTokenIssuer(
            secret=APPROVAL_SECRET,
            issuer=CONSOLE_ISSUER,
            audience=AUDIENCE,
        ),
        token_issuer=LocalTokenIssuer(
            secret=JWT_SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
        ),
        tenant_id=TENANT,
        default_approver=APPROVER,
    )
    return TestClient(create_console_app(service)), fake, service


def _run(client: TestClient, case_id: str) -> None:
    response = client.post(f"/cases/{case_id}/run", follow_redirects=True)
    assert response.status_code == 200


def _decide(
    client: TestClient,
    case_id: str,
    *,
    action: str,
    note: str = "",
    conditions: str = "",
):
    return client.post(
        f"/cases/{case_id}/decision",
        data={
            "action": action,
            "approver": APPROVER,
            "note": note,
            "conditions": conditions,
        },
        follow_redirects=True,
    )


def test_case_queue_lists_onboarding_and_invoice_cases(generated_corpus, tmp_path) -> None:
    client, _fake, _service = _console(generated_corpus, tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "Underwriting review console" in response.text
    assert ONBOARDING_CASE in response.text
    assert INVOICE_HIGH in response.text
    assert "Onboarding" in response.text
    assert "Invoice" in response.text
    assert "Ridgeline Haulage" in response.text


def test_run_case_shows_pending_approval_with_evidence(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)

    _run(client, ONBOARDING_CASE)
    response = client.get(f"/cases/{ONBOARDING_CASE}")

    assert response.status_code == 200
    assert "Pending approval" in response.text
    assert "Carrier Application / KYC" in response.text
    assert "Certificate of Insurance" in response.text
    assert "Legal name" in response.text
    assert "DOC-ONB-RIDGELINE-APP" in response.text
    assert "Page 1" in response.text
    assert "Approve" in response.text
    assert "Conditional approve" in response.text
    assert "Decline" in response.text
    assert "Request documents" in response.text
    assert "Recommended: approve" in response.text
    assert fake.decision_calls == []


def test_approve_mints_tokens_resumes_and_shows_the_audit(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_CASE)

    response = _decide(
        client,
        ONBOARDING_CASE,
        action="approve",
        note="Every field was checked against its citation.",
    )

    assert response.status_code == 200
    assert "Approved" in response.text
    assert "write_committed" in response.text
    assert "Audit chain verified" in response.text
    assert fake.read_case(ONBOARDING_CASE).status == "approved"
    assert len(fake.decision_calls) == 1
    call = fake.decision_calls[0]
    assert call["decision"] == "approve"
    assert call["approval_token"]
    assert call["approval_jwt"]
    assert call["idempotency_key"].startswith("idem_")

    approval_claims = jwt.decode(
        call["approval_token"],
        APPROVAL_SECRET,
        algorithms=["HS256"],
        audience=AUDIENCE,
        issuer=CONSOLE_ISSUER,
    )
    assert approval_claims["sub"] == APPROVER
    assert approval_claims["case_id"] == ONBOARDING_CASE
    assert approval_claims["decision"] == "approve"
    assert approval_claims["tid"] == TENANT

    bearer_claims = jwt.decode(
        call["approval_jwt"],
        JWT_SECRET,
        algorithms=["HS256"],
        audience=AUDIENCE,
        issuer=ISSUER,
    )
    assert "freight:approve" in bearer_claims["scope"].split()
    assert "freight:read" in bearer_claims["scope"].split()
    assert bearer_claims["tid"] == TENANT


def test_conditional_approve_requires_and_records_conditions(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_MEDIUM)

    refused = _decide(client, ONBOARDING_MEDIUM, action="conditional_approve")
    assert refused.status_code == 409
    assert "at least one condition" in refused.text.lower()
    assert fake.decision_calls == []

    response = _decide(
        client,
        ONBOARDING_MEDIUM,
        action="conditional_approve",
        conditions="Obtain six months of authority history.",
        note="Condition accepted by the reviewer.",
    )

    assert response.status_code == 200
    assert "Conditionally approved" in response.text
    assert len(fake.decision_calls) == 1
    call = fake.decision_calls[0]
    assert call["decision"] == "conditional_approve"
    assert call["conditions"] == ["Obtain six months of authority history."]


def test_decline_a_hard_stop_records_a_rejection(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_HARD_STOP)

    detail = client.get(f"/cases/{ONBOARDING_HARD_STOP}")
    assert "Hard stop" in detail.text
    assert "blocks approval" in detail.text.lower()

    response = _decide(
        client,
        ONBOARDING_HARD_STOP,
        action="decline",
        note="Insurance expired before the as-of date.",
    )

    assert response.status_code == 200
    assert "Declined" in response.text
    assert len(fake.decision_calls) == 1
    assert fake.decision_calls[0]["decision"] == "decline"


def test_approving_a_hard_stop_is_refused_by_the_service(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_HARD_STOP)

    response = _decide(
        client,
        ONBOARDING_HARD_STOP,
        action="approve",
        note="Trying to bypass the hard stop.",
    )

    assert response.status_code == 409
    assert "hard-stop" in response.text.lower()
    assert fake.decision_calls == []
    assert fake.read_case(ONBOARDING_HARD_STOP).decision is None


def test_requesting_documents_on_a_hard_stop_is_also_refused(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_HARD_STOP)

    response = _decide(
        client,
        ONBOARDING_HARD_STOP,
        action="request_documents",
        note="A hard stop cannot be parked as a document request.",
    )

    assert response.status_code == 409
    assert fake.decision_calls == []


def test_request_documents_path_sets_awaiting_documents(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)
    _run(client, INVOICE_MISSING_DOCUMENT)

    response = _decide(
        client,
        INVOICE_MISSING_DOCUMENT,
        action="request_documents",
        note="The signed proof of delivery is missing.",
        conditions="Obtain a signed proof of delivery.",
    )

    assert response.status_code == 200
    assert "Awaiting documents" in response.text
    assert len(fake.decision_calls) == 1
    assert fake.decision_calls[0]["decision"] == "request_documents"
    assert fake.read_case(INVOICE_MISSING_DOCUMENT).status == "awaiting_documents"


def test_duplicate_decision_post_returns_the_existing_decision(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_CASE)
    first = _decide(client, ONBOARDING_CASE, action="approve", note="Approved once.")
    assert first.status_code == 200

    second = _decide(client, ONBOARDING_CASE, action="decline", note="A second try.")

    assert second.status_code == 200
    assert "Approved" in second.text
    assert len(fake.decision_calls) == 1


def test_console_refuses_a_second_write_if_the_gateway_already_committed(
    generated_corpus, tmp_path
) -> None:
    client, fake, service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_CASE)
    pending = service.runner.get(ONBOARDING_CASE)
    assert pending is not None and pending.recommendation is not None

    # Simulate a crash after the gateway commit but before the graph checkpoint.
    fake.create_decision(
        DecisionCommand(
            case_id=ONBOARDING_CASE,
            decision="approve",
            rationale=pending.recommendation.rationale,
            conditions=[],
            approval_token="simulated-approval-token",
            idempotency_key="simulated-idempotency-key",
            approval_jwt="simulated-approval-jwt",
        )
    )

    response = _decide(client, ONBOARDING_CASE, action="approve", note="Retry after a crash.")

    assert response.status_code == 200
    assert "Approved" in response.text
    assert "Audit chain verified" in response.text
    assert len(fake.decision_calls) == 1
    reconciled = service.runner.get(ONBOARDING_CASE)
    assert reconciled is not None
    assert reconciled.status == "approved"
    assert reconciled.audit_verified is True


def test_unknown_case_is_not_found(generated_corpus, tmp_path) -> None:
    client, _fake, _service = _console(generated_corpus, tmp_path)

    response = client.get("/cases/case_missing")

    assert response.status_code == 404
    assert "Case not found" in response.text


def test_audit_partial_shows_the_committed_chain(generated_corpus, tmp_path) -> None:
    client, _fake, _service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_CASE)
    _decide(client, ONBOARDING_CASE, action="approve", note="Audit refresh.")

    response = client.get(f"/cases/{ONBOARDING_CASE}/audit")

    assert response.status_code == 200
    assert "write_committed" in response.text
    assert "create_decision" in response.text


def test_hold_recommendation_cannot_be_approved(generated_corpus, tmp_path) -> None:
    client, fake, service = _console(generated_corpus, tmp_path)
    _run(client, INVOICE_HIGH)
    detail = client.get(f"/cases/{INVOICE_HIGH}")

    assert detail.status_code == 200
    assert "The recommendation is hold" in detail.text
    assert 'value="approve"' in detail.text
    assert re.search(r'value="approve"[^>]*disabled', detail.text)

    response = _decide(
        client,
        INVOICE_HIGH,
        action="approve",
        note="Trying to approve a hold.",
    )

    assert response.status_code == 409
    assert "recommendation is hold" in response.text.lower()
    assert fake.decision_calls == []
    current = service.runner.get(INVOICE_HIGH)
    assert current is not None and current.decision is None


def test_failed_run_can_be_retried_from_the_console(generated_corpus, tmp_path) -> None:
    client, fake, service = _console(generated_corpus, tmp_path)
    fake.fail_next_recommendation("recommendation_unavailable", "Temporary gateway failure.")

    failed = client.post(f"/cases/{ONBOARDING_CASE}/run", follow_redirects=True)

    assert failed.status_code == 502
    assert "Failed" in failed.text
    assert "Retry run" in failed.text
    current = service.runner.get(ONBOARDING_CASE)
    assert current is not None and current.status == "failed"
    queue = client.get("/")
    assert "Failed" in queue.text

    retried = client.post(f"/cases/{ONBOARDING_CASE}/retry", follow_redirects=True)

    assert retried.status_code == 200
    assert "Pending approval" in retried.text
    current = service.runner.get(ONBOARDING_CASE)
    assert current is not None and current.status == "pending_approval"

    approved = _decide(client, ONBOARDING_CASE, action="approve", note="Recovered run.")
    assert approved.status_code == 200
    assert "Approved" in approved.text
    assert len(fake.decision_calls) == 1


def test_concurrent_decisions_write_once(generated_corpus, tmp_path) -> None:
    client, fake, service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_CASE)

    def submit(note: str):
        return service.decide(
            ONBOARDING_CASE,
            action="approve",
            approver=APPROVER,
            note=note,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["first concurrent review", "second concurrent review"]))

    assert all(result.status == "approved" for result in results)
    assert len(fake.decision_calls) == 1
    commits = [
        event
        for event in fake.get_audit(ONBOARDING_CASE).events
        if event.event == "write_committed"
    ]
    assert len(commits) == 1
    current = service.runner.get(ONBOARDING_CASE)
    assert current is not None and current.audit_verified is True


def test_expired_approval_returns_to_the_review_form(generated_corpus, tmp_path) -> None:
    client, fake, _service = _console(generated_corpus, tmp_path)
    _run(client, ONBOARDING_CASE)
    fake.fail_next_approval("approval_expired", "The approval token has expired.")

    refused = _decide(client, ONBOARDING_CASE, action="approve", note="Expired attempt.")

    assert refused.status_code == 200
    assert "Pending approval" in refused.text
    assert "Approval refused" in refused.text
    assert "approval expired" in refused.text
    assert len(fake.decision_calls) == 1

    approved = _decide(client, ONBOARDING_CASE, action="approve", note="Fresh attempt.")

    assert approved.status_code == 200
    assert "Approved" in approved.text
    assert len(fake.decision_calls) == 2
