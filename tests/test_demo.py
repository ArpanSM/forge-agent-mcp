from __future__ import annotations

from argparse import Namespace

from forge_agent_mcp.console.demo import DEMO_CASES, run_demo
from forge_agent_mcp.gateway.store import GatewayStore
from tests.conftest import APPROVER, MANIFEST_PATH, TENANT


def test_run_demo_completes_the_onboarding_and_invoice_cases(
    generated_corpus, tmp_path, capsys
) -> None:
    _manifest, corpus = generated_corpus
    demo_dir = tmp_path / "demo"
    args = Namespace(
        manifest=str(MANIFEST_PATH),
        corpus_dir=str(corpus.root),
        demo_dir=str(demo_dir),
        gateway_host="127.0.0.1",
        gateway_port=0,
        console_host="127.0.0.1",
        console_port=0,
        approver=APPROVER,
        keep=False,
        auto=True,
        serve=False,
    )

    assert run_demo(args) == 0

    store = GatewayStore(demo_dir / "gateway.db")
    try:
        decisions = {case_id: store.latest_decision(TENANT, case_id) for case_id in DEMO_CASES}
        stages = {case_id: store.latest_crm_stage(TENANT, case_id) for case_id in DEMO_CASES}
    finally:
        store.close()

    assert all(decision is not None for decision in decisions.values())
    assert decisions["case_onb_ridgeline"].decision == "approve"  # type: ignore[union-attr]
    assert decisions["case_inv_northgate"].decision == "request_documents"  # type: ignore[union-attr]
    assert stages["case_onb_ridgeline"].stage == "approved"  # type: ignore[union-attr]
    assert stages["case_inv_northgate"].stage == "awaiting_documents"  # type: ignore[union-attr]
    assert (demo_dir / "checkpoints.db").is_file()

    output = capsys.readouterr().out
    assert "Completed decisions:" in output
    assert "case_onb_ridgeline: approved" in output
    assert "case_inv_northgate: awaiting_documents" in output
