from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from forge_agent_mcp.agent.gateway import FastMCPGatewayClient
from forge_agent_mcp.agent.graph import CaseRunner
from forge_agent_mcp.console.app import create_console_app
from forge_agent_mcp.console.models import CaseCatalogEntry
from forge_agent_mcp.console.service import ConsoleService
from forge_agent_mcp.gateway.auth import READ_SCOPE, WRITE_SCOPE
from tests.conftest import APPROVER, TENANT

CASE_ID = "case_onb_ridgeline"


def test_console_runs_and_approves_a_case_against_the_real_gateway(
    gateway, generated_corpus, tmp_path: Path
) -> None:
    manifest, corpus = generated_corpus
    token = gateway.token(subject="agent-underwriter", scopes=[READ_SCOPE, WRITE_SCOPE])
    runner = CaseRunner(
        FastMCPGatewayClient(gateway.url, token),
        corpus_dir=corpus.root,
        checkpoint_path=tmp_path / "checkpoints.db",
    )
    service = ConsoleService(
        runner=runner,
        gateway=FastMCPGatewayClient(gateway.url, gateway.token(subject="console-reader")),
        catalog=[
            CaseCatalogEntry(
                case_id=CASE_ID,
                case_type="onboarding",
                carrier_name=manifest.carrier_by_id["car_ridgeline"].legal_name,
            )
        ],
        approval_issuer=gateway.approval,
        token_issuer=gateway.issuer,
        tenant_id=TENANT,
        default_approver=APPROVER,
    )
    client = TestClient(create_console_app(service))

    queue = client.get("/")
    assert queue.status_code == 200
    assert CASE_ID in queue.text

    run = client.post(f"/cases/{CASE_ID}/run", follow_redirects=True)
    assert run.status_code == 200
    assert "Pending approval" in run.text

    decision = client.post(
        f"/cases/{CASE_ID}/decision",
        data={
            "action": "approve",
            "approver": APPROVER,
            "note": "Approved through the real gateway console smoke test.",
            "conditions": "",
        },
        follow_redirects=True,
    )

    assert decision.status_code == 200
    assert "Approved" in decision.text
    assert "Audit chain verified" in decision.text
    case = gateway.store.get_case(TENANT, CASE_ID)
    assert case is not None and case.status == "approved"
    assert gateway.store.latest_decision(TENANT, CASE_ID) is not None
    events = gateway.store.list_audit(TENANT, CASE_ID)
    assert [event for event in events if event.event == "write_committed"]
