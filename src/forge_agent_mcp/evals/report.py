"""Build the JSON report and its Markdown companion."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .dataset import EvalDataset
from .metrics import aggregate_metrics, evaluate_case
from .models import (
    CaseComparison,
    CaseEvaluation,
    DatasetInfo,
    EvalCase,
    EvalReport,
    FailureItem,
    HardGate,
    MetricPair,
    PipelineName,
    PipelineRun,
    PipelineSummary,
    ProviderInfo,
    SplitMetrics,
)

METRIC_ORDER = (
    "classification_accuracy",
    "field_f1_clean",
    "field_f1_noisy",
    "citation_accuracy",
    "hard_stop_recall",
    "false_approve_rate",
    "red_flag_precision",
    "red_flag_recall",
    "decision_agreement",
    "approval_bypass_rate",
    "duplicate_write_rate",
    "audit_completeness",
    "injection_resistance",
    "tool_call_success",
    "latency_p50_ms",
    "latency_p95_ms",
    "token_cost_usd",
)


def _summary(
    pipeline: PipelineName,
    runs: list[PipelineRun],
    *,
    input_price_per_1k: float,
    output_price_per_1k: float,
) -> PipelineSummary:
    tokens_in = sum(run.tokens_in for run in runs)
    tokens_out = sum(run.tokens_out for run in runs)
    usd = (tokens_in * input_price_per_1k + tokens_out * output_price_per_1k) / 1000.0
    return PipelineSummary(
        pipeline=pipeline,
        cases=len(runs),
        completed=sum(1 for run in runs if run.status == "completed"),
        failed=sum(1 for run in runs if run.status == "failed"),
        decisions=sum(1 for run in runs if run.write_committed),
        approvals=sum(1 for run in runs if run.approval_present),
        write_commits=sum(1 for run in runs if run.write_committed),
        duplicate_writes=sum(max(0, run.decision_rows - 1) for run in runs),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        total_tokens=tokens_in + tokens_out,
        tool_calls_attempted=sum(run.tool_calls.attempted for run in runs),
        tool_calls_succeeded=sum(run.tool_calls.succeeded for run in runs),
        tool_calls_failed=sum(run.tool_calls.failed for run in runs),
        duration_ms_total=sum(run.latency_ms for run in runs),
        estimated_usd=usd,
    )


def build_split_metrics(
    *,
    split: str,
    cases: list[EvalCase],
    baseline_runs: dict[str, PipelineRun],
    fixed_runs: dict[str, PipelineRun],
    seed: int,
    input_price_per_1k: float = 0.0,
    output_price_per_1k: float = 0.0,
) -> SplitMetrics:
    baseline_evaluations = [evaluate_case(case, baseline_runs[case.case_id]) for case in cases]
    fixed_evaluations = [evaluate_case(case, fixed_runs[case.case_id]) for case in cases]
    baseline_metrics = aggregate_metrics(
        baseline_evaluations,
        seed=seed,
        input_price_per_1k=input_price_per_1k,
        output_price_per_1k=output_price_per_1k,
    )
    fixed_metrics = aggregate_metrics(
        fixed_evaluations,
        seed=seed,
        input_price_per_1k=input_price_per_1k,
        output_price_per_1k=output_price_per_1k,
    )
    names = [name for name in METRIC_ORDER if name in fixed_metrics]
    names.extend(sorted(set(fixed_metrics) - set(names)))
    metrics = {
        name: MetricPair(
            metric=name,
            baseline=baseline_metrics[name],
            fixed=fixed_metrics[name],
        )
        for name in names
    }
    return SplitMetrics(
        split=split,  # type: ignore[arg-type]
        case_count=len(cases),
        metrics=metrics,
        baseline=_summary(
            "baseline",
            [baseline_runs[case.case_id] for case in cases],
            input_price_per_1k=input_price_per_1k,
            output_price_per_1k=output_price_per_1k,
        ),
        fixed=_summary(
            "fixed",
            [fixed_runs[case.case_id] for case in cases],
            input_price_per_1k=input_price_per_1k,
            output_price_per_1k=output_price_per_1k,
        ),
    )


def build_case_comparisons(
    cases: list[EvalCase],
    baseline_runs: dict[str, PipelineRun],
    fixed_runs: dict[str, PipelineRun],
) -> list[CaseComparison]:
    return [
        CaseComparison(
            case_id=case.case_id,
            split=case.split,
            case_type=case.case_type,
            category=case.category,
            archetypes=list(case.archetypes),
            expected_recommendation=case.expected_recommendation,
            expected_decision=case.expected_decision,
            expected_flags=list(case.expected_flags),
            baseline=baseline_runs[case.case_id],
            fixed=fixed_runs[case.case_id],
        )
        for case in cases
    ]


def _failure(
    *,
    pipeline: PipelineName,
    case: EvalCase,
    evaluation: CaseEvaluation,
    reason: str,
    expected: str,
    actual: str,
) -> FailureItem:
    return FailureItem(
        pipeline=pipeline,
        case_id=case.case_id,
        split=case.split,
        category=case.category,
        reason=reason,
        expected=expected,
        actual=actual,
    )


def build_failure_gallery(
    cases: list[EvalCase],
    baseline_runs: dict[str, PipelineRun],
    fixed_runs: dict[str, PipelineRun],
) -> list[FailureItem]:
    """Collect high-signal failures; the baseline is expected to dominate."""

    items: list[FailureItem] = []
    for case in cases:
        baseline = baseline_runs[case.case_id]
        fixed = fixed_runs[case.case_id]
        baseline_eval = evaluate_case(case, baseline)
        fixed_eval = evaluate_case(case, fixed)

        if not fixed_eval.decision_correct:
            items.append(
                _failure(
                    pipeline="fixed",
                    case=case,
                    evaluation=fixed_eval,
                    reason="decision disagrees with the oracle",
                    expected=case.expected_decision,
                    actual=str(fixed.decision),
                )
            )
        if fixed_eval.false_approve:
            items.append(
                _failure(
                    pipeline="fixed",
                    case=case,
                    evaluation=fixed_eval,
                    reason="hard stop was approved",
                    expected="decline or hold",
                    actual=str(fixed.decision),
                )
            )
        if case.has_injection and not fixed_eval.injection_resisted:
            items.append(
                _failure(
                    pipeline="fixed",
                    case=case,
                    evaluation=fixed_eval,
                    reason="document injection was not resisted",
                    expected="injection flagged and no approval",
                    actual=f"flags={fixed.flags} decision={fixed.decision}",
                )
            )
        if fixed_eval.duplicate_write:
            items.append(
                _failure(
                    pipeline="fixed",
                    case=case,
                    evaluation=fixed_eval,
                    reason="retry duplicated the decision",
                    expected="one decision row",
                    actual=f"{fixed.decision_rows} decision row(s)",
                )
            )
        if fixed_eval.approval_bypass:
            items.append(
                _failure(
                    pipeline="fixed",
                    case=case,
                    evaluation=fixed_eval,
                    reason="decision write bypassed approval",
                    expected="an approval token and audit record",
                    actual="no approval evidence",
                )
            )
        if not fixed_eval.audit_complete:
            items.append(
                _failure(
                    pipeline="fixed",
                    case=case,
                    evaluation=fixed_eval,
                    reason="audit trail is incomplete",
                    expected="read, extraction, recommendation and one committed decision",
                    actual=f"audit_verified={fixed.audit_verified}",
                )
            )
        if fixed_eval.classification_correct < fixed_eval.classification_total:
            items.append(
                _failure(
                    pipeline="fixed",
                    case=case,
                    evaluation=fixed_eval,
                    reason="document classification mismatch",
                    expected=f"{fixed_eval.classification_total} correct documents",
                    actual=f"{fixed_eval.classification_correct} correct",
                )
            )
        if fixed_eval.citation_correct < fixed_eval.citation_total:
            items.append(
                _failure(
                    pipeline="fixed",
                    case=case,
                    evaluation=fixed_eval,
                    reason="citation mismatch",
                    expected=f"{fixed_eval.citation_total} correct citations",
                    actual=f"{fixed_eval.citation_correct} correct",
                )
            )

        # The baseline's structural gaps are deliberate. Record its unsafe decisions
        # and let the aggregate metrics carry the rest.
        if not baseline_eval.decision_correct:
            items.append(
                _failure(
                    pipeline="baseline",
                    case=case,
                    evaluation=baseline_eval,
                    reason="naive baseline decision disagrees with the oracle",
                    expected=case.expected_decision,
                    actual=str(baseline.decision),
                )
            )
        if baseline_eval.false_approve:
            items.append(
                _failure(
                    pipeline="baseline",
                    case=case,
                    evaluation=baseline_eval,
                    reason="naive baseline approved a hard stop",
                    expected="decline or hold",
                    actual=str(baseline.decision),
                )
            )
    return items


def build_report(
    *,
    dataset: EvalDataset,
    provider: ProviderInfo,
    eval_metrics: SplitMetrics,
    held_out_metrics: SplitMetrics,
    gates: list[HardGate],
    cases: list[CaseComparison],
    failure_gallery: list[FailureItem],
    generated_at: datetime | None = None,
    caveats: list[str] | None = None,
) -> EvalReport:
    release_gate = "passed" if all(gate.passed for gate in gates) else "failed"
    default_caveats = [
        "The corpus is synthetic and watermarked; no real carrier, person or bank data is used.",
        "Token counts are character-based estimates because the provider contract "
        "has no usage field.",
        "The baseline always approves and writes directly; it has no citations, "
        "validators or approval.",
        "The fixed pipeline's duplicate-write check replays the identical decision "
        "write with the same key; the graph itself is checkpointed after the first commit.",
        "Held-out results are reported separately and are not part of the release hard gates.",
        "No LLM judge is used; all metrics are deterministic against the manifest "
        "and generated ground truth.",
    ]
    return EvalReport(
        generated_at=generated_at or datetime.now(UTC),
        provider=provider,
        dataset=DatasetInfo(
            manifest_path=str(dataset.manifest_path),
            manifest_hash=dataset.manifest_hash,
            corpus_dir=str(dataset.corpus_dir),
            as_of=dataset.manifest.as_of.isoformat(),
            eval_cases=len(dataset.split("eval")),
            held_out_cases=len(dataset.split("held_out")),
            carrier_count=dataset.carrier_count,
        ),
        eval=eval_metrics,
        held_out=held_out_metrics,
        gates=gates,
        release_gate=release_gate,
        cases=cases,
        failure_gallery=failure_gallery,
        caveats=caveats or default_caveats,
    )


def _fmt_metric(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if unit == "ms":
        return f"{value:.1f} ms"
    if unit == "usd":
        return f"${value:.4f}"
    return f"{value:.2f}"


def _metric_table(split: SplitMetrics) -> list[str]:
    lines = [
        "| Metric | n (baseline / fixed) | Baseline | Fixed | Delta | 95% interval (fixed) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for pair in split.metrics.values():
        baseline = pair.baseline
        fixed = pair.fixed
        delta = (
            f"{fixed.value - baseline.value:+.4f}"
            if fixed.value is not None and baseline.value is not None
            else "n/a"
        )
        interval = (
            f"[{_fmt_metric(fixed.ci_low, fixed.unit)}, {_fmt_metric(fixed.ci_high, fixed.unit)}]"
            if fixed.ci_low is not None and fixed.ci_high is not None
            else "n/a"
        )
        lines.append(
            f"| `{pair.metric}` | {baseline.n} / {fixed.n} | "
            f"{_fmt_metric(baseline.value, baseline.unit)} | "
            f"{_fmt_metric(fixed.value, fixed.unit)} | {delta} | {interval} |"
        )
    return lines


def _case_table(cases: list[CaseComparison]) -> list[str]:
    lines = [
        "| Case | Split | Category | Expected | Baseline | Fixed | Fixed flags | Audit | Rows |",
        "|---|---|---|---|---|---|---|---|---:|",
    ]
    for case in cases:
        lines.append(
            f"| `{case.case_id}` | {case.split} | {case.category} | "
            f"{case.expected_decision} | {case.baseline.decision or 'n/a'} | "
            f"{case.fixed.decision or 'n/a'} | {len(case.fixed.flags)} | "
            f"{'yes' if case.fixed.audit_complete else 'no'} | "
            f"{case.fixed.decision_rows} |"
        )
    return lines


def _summary_table(split: SplitMetrics) -> list[str]:
    return [
        "| Pipeline | Cases | Decisions | Approvals | Commits | Duplicate rows "
        "| Tokens | Tool calls | Total latency |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| baseline | {split.baseline.cases} | {split.baseline.decisions} | "
        f"{split.baseline.approvals} | {split.baseline.write_commits} | "
        f"{split.baseline.duplicate_writes} | {split.baseline.total_tokens} | "
        f"{split.baseline.tool_calls_attempted} | {split.baseline.duration_ms_total:.1f} ms |",
        f"| fixed | {split.fixed.cases} | {split.fixed.decisions} | "
        f"{split.fixed.approvals} | {split.fixed.write_commits} | "
        f"{split.fixed.duplicate_writes} | {split.fixed.total_tokens} | "
        f"{split.fixed.tool_calls_attempted} | {split.fixed.duration_ms_total:.1f} ms |",
    ]


def render_markdown(report: EvalReport) -> str:
    gate_label = "PASSED" if report.release_gate == "passed" else "FAILED"
    lines = [
        "# Evaluation report",
        "",
        f"**Release gate: {gate_label}**",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Generated | {report.generated_at.isoformat()} |",
        f"| Provider | `{report.provider.name}` / `{report.provider.model}` "
        f"({report.provider.mode}) |",
        f"| Manifest | `{report.dataset.manifest_hash}` |",
        f"| Cases | {report.dataset.eval_cases} eval + {report.dataset.held_out_cases} held out |",
        "",
        "## Baseline vs fixed (eval split)",
        "",
        "The baseline is one prompt over concatenated document text with no citations, validators,",
        "approval or audit. The fixed pipeline uses the eight-stage graph and the real "
        "MCP gateway.",
        "",
        *_summary_table(report.eval),
        "",
        *_metric_table(report.eval),
        "",
        "## Hard gates",
        "",
        "| Gate | Requirement | Baseline | Fixed | Status |",
        "|---|---|---:|---:|---|",
    ]
    for gate in report.gates:
        baseline = "yes" if gate.baseline_passed else "no"
        fixed = "yes" if gate.passed else "no"
        lines.append(
            f"| `{gate.gate_id}` | {gate.comparator} {gate.threshold:.2f} | "
            f"{gate.baseline_value!r} ({baseline}) | {gate.value!r} ({fixed}) | "
            f"{'PASS' if gate.passed else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Per-case results (eval split)",
            "",
            *_case_table([case for case in report.cases if case.split == "eval"]),
            "",
        ]
    )
    lines.extend(
        [
            "## Held-out results",
            "",
            "The four held-out carriers are reported separately and are not part of the "
            "release gate.",
            "",
            *_summary_table(report.held_out),
            "",
            *_metric_table(report.held_out),
            "",
            "### Held-out cases",
            "",
            *_case_table([case for case in report.cases if case.split == "held_out"]),
            "",
            "## Failure gallery",
            "",
        ]
    )
    fixed_failures = [item for item in report.failure_gallery if item.pipeline == "fixed"]
    baseline_failures = [item for item in report.failure_gallery if item.pipeline == "baseline"]
    if fixed_failures:
        lines.append("### Fixed pipeline")
        lines.append("")
        for item in fixed_failures:
            lines.append(
                f"- `{item.case_id}` ({item.category}): **{item.reason}** — "
                f"expected {item.expected}; got {item.actual}"
            )
        lines.append("")
    else:
        lines.extend(["### Fixed pipeline", "", "No fixed-pipeline failures.", ""])
    lines.append("### Baseline (expected failures)")
    lines.append("")
    if baseline_failures:
        for item in baseline_failures:
            lines.append(
                f"- `{item.case_id}` ({item.category}): **{item.reason}** — "
                f"expected {item.expected}; got {item.actual}"
            )
    else:
        lines.append("No baseline failures recorded.")
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    lines.append("")
    return "\n".join(lines)


def write_report(report: EvalReport, out_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "eval-report.json"
    markdown_path = directory / "eval-report.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def report_exit_code(report: EvalReport) -> int:
    """The release gate is the process exit code."""

    return 0 if report.release_gate == "passed" else 1


__all__ = [
    "METRIC_ORDER",
    "build_case_comparisons",
    "build_failure_gallery",
    "build_report",
    "build_split_metrics",
    "render_markdown",
    "report_exit_code",
    "write_report",
]
