from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forge_agent_mcp.corpus.vocab import Split
from forge_agent_mcp.evals.cli import build_provider_for_label, main
from forge_agent_mcp.evals.dataset import load_eval_dataset
from forge_agent_mcp.evals.models import (
    DatasetInfo,
    EvalReport,
    HardGate,
    MetricPair,
    MetricResult,
    PipelineName,
    PipelineSummary,
    PricingInfo,
    ProviderInfo,
    ReleaseGate,
    SplitMetrics,
)
from forge_agent_mcp.evals.report import report_exit_code
from forge_agent_mcp.evals.runner import EvalConfig, EvalRunError, run_evals
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


def _summary(pipeline: PipelineName) -> PipelineSummary:
    return PipelineSummary(
        pipeline=pipeline,
        cases=0,
        completed=0,
        failed=0,
        decisions=0,
        approvals=0,
        write_commits=0,
        duplicate_writes=0,
        tokens_in=0,
        tokens_out=0,
        total_tokens=0,
        tool_calls_attempted=0,
        tool_calls_succeeded=0,
        tool_calls_failed=0,
        duration_ms_total=0.0,
        estimated_usd=0.0,
    )


def _split(split: Split) -> SplitMetrics:
    metric = MetricResult(name="decision_agreement", value=0.0, n=0)
    pair = MetricPair(metric="decision_agreement", baseline=metric, fixed=metric)
    return SplitMetrics(
        split=split,
        case_count=0,
        metrics={"decision_agreement": pair},
        baseline=_summary("baseline"),
        fixed=_summary("fixed"),
    )


def _minimal_report(*, release_gate: ReleaseGate = "failed") -> EvalReport:
    gate = HardGate(
        gate_id="hard_stop_recall",
        metric="hard_stop_recall",
        comparator=">=",
        threshold=1.0,
        value=0.0,
        passed=False,
        detail="forced failure",
    )
    return EvalReport(
        generated_at=datetime.now(UTC),
        provider=ProviderInfo(name="fake", model="fake-model", mode="offline", details="test"),
        pricing=PricingInfo(
            mode="free",
            input_price_per_1k=0.0,
            output_price_per_1k=0.0,
            note="test free model",
        ),
        dataset=DatasetInfo(
            manifest_path=str(MANIFEST_PATH),
            manifest_hash="0" * 64,
            corpus_dir="data/corpus",
            as_of="2026-11-01",
            eval_cases=12,
            held_out_cases=4,
            eval_cases_run=0,
            held_out_cases_run=0,
            carrier_count=8,
            subset=False,
        ),
        eval=_split("eval"),
        held_out=_split("held_out"),
        gates=[gate],
        gate_applicable=True,
        release_gate=release_gate,
        cases=[],
        failure_gallery=[],
        caveats=[],
    )


def test_eval_smoke_builds_schema_json_and_markdown(generated_corpus, tmp_path: Path) -> None:
    _manifest, corpus = generated_corpus
    out_dir = tmp_path / "reports"

    report = run_evals(
        EvalConfig(
            manifest_path=MANIFEST_PATH,
            corpus_dir=corpus.root,
            out_dir=out_dir,
            case_ids=SMOKE_CASES,
            allow_subset=True,
            seed=7,
        )
    )

    assert report.release_gate == "not_applicable"
    assert report.gate_applicable is False
    assert report.dataset.subset is True
    assert report.dataset.eval_cases == 12
    assert report.dataset.held_out_cases == 4
    assert report.dataset.eval_cases_run == 3
    assert report.dataset.held_out_cases_run == 1
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
    assert len(report.approval_probes) == 6
    assert all(probe.blocked for probe in report.approval_probes)
    assert report.eval.metrics["duplicate_write_rate"].fixed.value == 0.0
    assert report.eval.metrics["audit_completeness"].fixed.value == 1.0
    assert report.eval.metrics["injection_resistance"].fixed.value == 1.0
    assert report.eval.metrics["classification_accuracy"].fixed.value == 1.0
    assert report.eval.metrics["citation_accuracy"].fixed.value == 1.0
    assert report.eval.metrics["field_f1_clean"].fixed.value == 1.0
    assert report.eval.metrics["field_f1_noisy"].fixed.value == 1.0
    assert all(gate.passed for gate in report.gates)
    assert all(gate.baseline_passed is False for gate in report.gates)
    assert report_exit_code(report) == 1

    dataset = load_eval_dataset(MANIFEST_PATH, corpus.root)
    clean_cases = [
        case for case in dataset.cases if case.case_id in SMOKE_CASES and case.category == "clean"
    ]
    clean_fields = sum(len(case.expected_fields) for case in clean_cases)
    assert report.eval.metrics["field_f1_clean"].fixed.n == clean_fields
    assert report.eval.metrics["field_f1_clean"].fixed.case_count == len(clean_cases)

    assert report.pricing.mode == "free"
    assert report.eval.fixed.estimated_usd == 0.0
    assert report.eval.metrics["token_cost_usd"].fixed.value == 0.0
    assert any("$0.00" in caveat for caveat in report.caveats)

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
    assert raw["release_gate"] == "not_applicable"
    assert raw["dataset"]["subset"] is True
    assert raw["dataset"]["eval_cases_run"] == 3
    assert raw["dataset"]["held_out_cases_run"] == 1

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
    assert "NOT_APPLICABLE" in markdown
    assert "subset run" in markdown
    assert "Subset run:" in markdown
    assert "| Gate applicable | no |" in markdown
    assert "Field F1 denominators" in markdown
    assert "Approval probes blocked: 6/6" in markdown
    assert "Fields (baseline / fixed)" in markdown
    assert "Cases (baseline / fixed)" in markdown
    assert "case_inv_quarry_road" in markdown
    held_out_section = markdown.index("### Baseline pipeline — held out")
    assert "case_inv_quarry_road" in markdown[held_out_section:]
    assert "manifest" in markdown.lower()


def test_exit_code_is_zero_only_for_a_passing_full_gate() -> None:
    not_applicable = _minimal_report(release_gate="not_applicable")
    failed = _minimal_report(release_gate="failed")
    passed = not_applicable.model_copy(update={"release_gate": "passed", "gate_applicable": True})

    assert report_exit_code(not_applicable) == 1
    assert report_exit_code(failed) == 1
    assert report_exit_code(passed) == 0


def test_priced_run_reports_a_real_estimated_cost(generated_corpus, tmp_path: Path) -> None:
    _manifest, corpus = generated_corpus
    report = run_evals(
        EvalConfig(
            manifest_path=MANIFEST_PATH,
            corpus_dir=corpus.root,
            out_dir=tmp_path / "priced",
            case_ids=("case_onb_ridgeline",),
            allow_subset=True,
            input_price_per_1k=1.0,
            output_price_per_1k=2.0,
            seed=7,
        )
    )

    assert report.pricing.mode == "priced"
    assert report.pricing.input_price_per_1k == 1.0
    assert report.pricing.output_price_per_1k == 2.0
    assert report.eval.fixed.estimated_usd is not None
    assert report.eval.fixed.estimated_usd > 0
    assert report.eval.metrics["token_cost_usd"].fixed.value is not None
    assert report.eval.metrics["token_cost_usd"].fixed.value > 0


def test_cli_returns_nonzero_when_a_fixed_gate_fails(monkeypatch) -> None:
    import forge_agent_mcp.evals.cli as cli_module

    failed = _minimal_report(release_gate="failed")
    monkeypatch.setattr(cli_module, "build_provider_for_label", lambda label: FakeProvider())
    monkeypatch.setattr(cli_module, "run_evals", lambda config, provider: failed)

    assert main(["--no-write"]) == 1


def test_live_provider_refuses_to_start_without_a_key(monkeypatch, tmp_path: Path) -> None:
    from forge_agent_mcp.evals.runner import EvalRunError

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)
    with pytest.raises(EvalRunError, match="ORCAROUTER_API_KEY"):
        build_provider_for_label("orcarouter")


def test_subset_run_without_allow_subset_is_refused(generated_corpus, tmp_path: Path) -> None:
    _manifest, corpus = generated_corpus
    with pytest.raises(EvalRunError, match="allow_subset"):
        run_evals(
            EvalConfig(
                manifest_path=MANIFEST_PATH,
                corpus_dir=corpus.root,
                out_dir=tmp_path / "reports",
                case_ids=("case_onb_ridgeline",),
            )
        )
