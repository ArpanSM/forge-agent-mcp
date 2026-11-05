from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from forge_agent_mcp.evals.models import EvalReport
from forge_agent_mcp.evals.report import report_exit_code
from forge_agent_mcp.evals.runner import EvalConfig, run_evals
from forge_agent_mcp.providers import FakeProvider
from tests.conftest import MANIFEST_PATH

SMOKE_CASES = (
    "case_onb_ridgeline",
    "case_onb_northgate",
    "case_onb_meridian",
    "case_inv_quarry_road",
)
REQUIRED_METRICS = {
    "classification_accuracy",
    "field_f1_clean",
    "field_f1_noisy",
    "citation_accuracy",
    "hard_stop_recall",
    "false_approve_rate",
    "red_flag_precision",
    "decision_agreement",
    "approval_bypass_rate",
    "duplicate_write_rate",
    "audit_completeness",
    "injection_resistance",
    "tool_call_success",
    "latency_p50_ms",
    "latency_p95_ms",
    "token_cost_usd",
}


def test_eval_smoke_builds_schema_json_and_markdown(generated_corpus, tmp_path: Path) -> None:
    _manifest, corpus = generated_corpus
    out_dir = tmp_path / "reports"

    report = run_evals(
        EvalConfig(
            manifest_path=MANIFEST_PATH,
            corpus_dir=corpus.root,
            out_dir=out_dir,
            case_ids=SMOKE_CASES,
            seed=7,
        )
    )

    assert report.release_gate == "passed"
    assert report.provider.mode == "offline"
    assert len(report.dataset.manifest_hash) == 64
    assert report.dataset.eval_cases == 12
    assert report.dataset.held_out_cases == 4
    assert report.eval.case_count == 3
    assert report.held_out.case_count == 1
    assert set(report.eval.metrics) >= REQUIRED_METRICS
    assert set(report.held_out.metrics) >= REQUIRED_METRICS

    assert report.eval.metrics["decision_agreement"].fixed.value == 1.0
    assert report.eval.metrics["decision_agreement"].baseline.value == 1 / 3
    assert report.eval.metrics["hard_stop_recall"].fixed.value == 1.0
    assert report.eval.metrics["false_approve_rate"].fixed.value == 0.0
    assert report.eval.metrics["false_approve_rate"].baseline.value == 1.0
    assert report.eval.metrics["approval_bypass_rate"].fixed.value == 0.0
    assert report.eval.metrics["duplicate_write_rate"].fixed.value == 0.0
    assert report.eval.metrics["audit_completeness"].fixed.value == 1.0
    assert report.eval.metrics["injection_resistance"].fixed.value == 1.0
    assert report.eval.metrics["classification_accuracy"].fixed.value == 1.0
    assert report.eval.metrics["citation_accuracy"].fixed.value == 1.0
    assert report.eval.metrics["field_f1_clean"].fixed.value == 1.0
    assert report.eval.metrics["field_f1_noisy"].fixed.value == 1.0
    assert all(gate.passed for gate in report.gates)
    assert all(gate.baseline_passed is False for gate in report.gates)

    by_case = {case.case_id: case for case in report.cases}
    assert by_case["case_onb_ridgeline"].fixed.decision == "approve"
    assert by_case["case_onb_northgate"].fixed.decision == "decline"
    assert by_case["case_onb_meridian"].fixed.decision == "request_documents"
    assert not [item for item in report.failure_gallery if item.pipeline == "fixed"]
    assert [item for item in report.failure_gallery if item.pipeline == "baseline"]

    json_path = out_dir / "eval-report.json"
    markdown_path = out_dir / "eval-report.md"
    assert json_path.is_file()
    assert markdown_path.is_file()
    loaded = EvalReport.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert loaded == report
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    assert raw["release_gate"] == "passed"
    assert raw["dataset"]["manifest_hash"] == report.dataset.manifest_hash

    markdown = markdown_path.read_text(encoding="utf-8")
    for heading in (
        "# Evaluation report",
        "## Baseline vs fixed (eval split)",
        "## Hard gates",
        "## Per-case results",
        "## Held-out results",
        "## Failure gallery",
        "## Caveats",
    ):
        assert heading in markdown
    assert "case_inv_quarry_road" in markdown
    assert "manifest" in markdown.lower()


def test_exit_code_is_nonzero_when_the_release_gate_fails(generated_corpus, tmp_path: Path) -> None:
    _manifest, corpus = generated_corpus
    report = run_evals(
        EvalConfig(
            manifest_path=MANIFEST_PATH,
            corpus_dir=corpus.root,
            out_dir=tmp_path / "reports",
            case_ids=SMOKE_CASES,
            seed=7,
        )
    )
    assert report_exit_code(report) == 0

    failed = report.model_copy(update={"release_gate": "failed"})
    assert report_exit_code(failed) == 1


def test_cli_returns_nonzero_when_a_fixed_gate_fails(monkeypatch) -> None:
    import forge_agent_mcp.evals.runner as runner_module

    failed = SimpleNamespace(
        provider=SimpleNamespace(name="fake", model="fake-model", mode="offline"),
        gates=[
            SimpleNamespace(
                passed=False,
                gate_id="hard_stop_recall",
                value=0.0,
                comparator=">=",
                threshold=1.0,
            )
        ],
        eval=SimpleNamespace(
            metrics={
                "decision_agreement": SimpleNamespace(
                    baseline=SimpleNamespace(value=0.0),
                    fixed=SimpleNamespace(value=0.0),
                )
            }
        ),
        release_gate="failed",
    )
    monkeypatch.setattr(runner_module, "build_provider_for_label", lambda label: FakeProvider())
    monkeypatch.setattr(runner_module, "run_evals", lambda config, provider: failed)

    assert runner_module.main(["--no-write"]) == 1


def test_live_provider_refuses_to_start_without_a_key(monkeypatch, tmp_path: Path) -> None:
    import pytest

    from forge_agent_mcp.evals.runner import EvalRunError, build_provider_for_label

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)
    with pytest.raises(EvalRunError, match="ORCAROUTER_API_KEY"):
        build_provider_for_label("orcarouter")
