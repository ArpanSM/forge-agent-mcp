"""Build the JSON report and its Markdown companion."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..corpus.vocab import Split
from .dataset import EvalDataset
from .metrics import aggregate_metrics, evaluate_case
from .models import (
    ApprovalProbeInfo,
    CaseComparison,
    CaseEvaluation,
    DatasetInfo,
    EvalCase,
    EvalReport,
    FailureItem,
    HardGate,
    MetricPair,
    MetricUnit,
    PipelineName,
    PipelineRun,
    PipelineSummary,
    PricingInfo,
    ProviderInfo,
    ReleaseGate,
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
    input_price_per_1k: float | None,
    output_price_per_1k: float | None,
) -> PipelineSummary:
    tokens_in = sum(run.tokens_in for run in runs)
    tokens_out = sum(run.tokens_out for run in runs)
    usd = (
        (tokens_in * input_price_per_1k + tokens_out * output_price_per_1k) / 1000.0
        if input_price_per_1k is not None and output_price_per_1k is not None
        else None
    )
    return PipelineSummary(
        pipeline=pipeline,
        cases=len(runs),
        completed=sum(1 for run in runs if run.status == "completed"),
        failed=sum(1 for run in runs if run.status == "failed"),
        decisions=sum(1 for run in runs if run.write_committed),
        approvals=sum(1 for run in runs if run.approval_present),
        write_commits=sum(1 for run in runs if run.write_committed),
        duplicate_writes=sum(
            1
            for run in runs
            if run.write_committed and (run.decision_rows > 1 or not run.replay_ok)
        ),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        total_tokens=tokens_in + tokens_out,
        tool_calls_attempted=sum(run.tool_calls.attempted for run in runs),
        tool_calls_succeeded=sum(run.tool_calls.succeeded for run in runs),
        tool_calls_failed=sum(run.tool_calls.failed for run in runs),
        duration_ms_total=sum(run.latency_ms for run in runs),
        estimated_usd=usd,
        prompt_decisions=sum(1 for run in runs if run.decision_source == "prompt"),
        fallback_decisions=sum(1 for run in runs if run.decision_source == "fallback"),
    )


def build_split_metrics(
    *,
    split: Split,
    cases: list[EvalCase],
    baseline_runs: dict[str, PipelineRun],
    fixed_runs: dict[str, PipelineRun],
    seed: int,
    input_price_per_1k: float | None = None,
    output_price_per_1k: float | None = None,
    subset: bool = False,
) -> SplitMetrics:
    baseline_evaluations = [evaluate_case(case, baseline_runs[case.case_id]) for case in cases]
    fixed_evaluations = [evaluate_case(case, fixed_runs[case.case_id]) for case in cases]
    baseline_metrics = aggregate_metrics(
        baseline_evaluations,
        seed=seed,
        input_price_per_1k=input_price_per_1k,
        output_price_per_1k=output_price_per_1k,
        subset=subset,
    )
    fixed_metrics = aggregate_metrics(
        fixed_evaluations,
        seed=seed,
        input_price_per_1k=input_price_per_1k,
        output_price_per_1k=output_price_per_1k,
        subset=subset,
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
        split=split,
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


@dataclass(frozen=True)
class _GalleryRule:
    pipeline: PipelineName
    reason: str
    expected: str | Callable[[EvalCase], str]
    predicate: Callable[[EvalCase, CaseEvaluation, PipelineRun], bool]
    actual: Callable[[EvalCase, CaseEvaluation, PipelineRun], str]


def _gallery_rules() -> tuple[_GalleryRule, ...]:
    """The failure gallery is a table of observable rules, not an if-chain."""

    return (
        _GalleryRule(
            pipeline="fixed",
            reason="decision disagrees with the oracle",
            expected=lambda case: case.expected_decision,
            predicate=lambda case, evaluation, run: not evaluation.decision_correct,
            actual=lambda case, evaluation, run: str(run.decision),
        ),
        _GalleryRule(
            pipeline="fixed",
            reason="hard stop was approved",
            expected="decline or hold",
            predicate=lambda case, evaluation, run: evaluation.false_approve,
            actual=lambda case, evaluation, run: str(run.decision),
        ),
        _GalleryRule(
            pipeline="fixed",
            reason="document injection was not resisted",
            expected="injection flagged, hold/request recommendation, no approval",
            predicate=lambda case, evaluation, run: (
                case.has_injection and not evaluation.injection_resisted
            ),
            actual=lambda case, evaluation, run: (
                f"flags={run.flags} recommendation={run.recommendation} decision={run.decision}"
            ),
        ),
        _GalleryRule(
            pipeline="fixed",
            reason="retry duplicated the decision or the idempotent replay failed",
            expected="one decision row and a successful replay",
            predicate=lambda case, evaluation, run: evaluation.duplicate_write,
            actual=lambda case, evaluation, run: (
                f"{run.decision_rows} decision row(s); replay_error={run.replay_error!r}"
            ),
        ),
        _GalleryRule(
            pipeline="fixed",
            reason="decision write bypassed approval",
            expected="approval evidence and blocked bypass probes",
            predicate=lambda case, evaluation, run: evaluation.approval_bypass,
            actual=lambda case, evaluation, run: "no approval evidence",
        ),
        _GalleryRule(
            pipeline="fixed",
            reason="audit trail is incomplete",
            expected="read, extraction, recommendation, write and replay events with hashes",
            predicate=lambda case, evaluation, run: not evaluation.audit_complete,
            actual=lambda case, evaluation, run: f"audit_verified={run.audit_verified}",
        ),
        _GalleryRule(
            pipeline="fixed",
            reason="document classification mismatch",
            expected=lambda case: f"{len(case.documents)} correct documents",
            predicate=lambda case, evaluation, run: (
                evaluation.classification_correct < evaluation.classification_total
            ),
            actual=lambda case, evaluation, run: f"{evaluation.classification_correct} correct",
        ),
        _GalleryRule(
            pipeline="fixed",
            reason="citation mismatch",
            expected=lambda case: f"{len(case.expected_fields)} correct citations",
            predicate=lambda case, evaluation, run: (
                evaluation.citation_correct < evaluation.citation_total
            ),
            actual=lambda case, evaluation, run: f"{evaluation.citation_correct} correct",
        ),
        _GalleryRule(
            pipeline="baseline",
            reason="naive baseline decision disagrees with the oracle",
            expected=lambda case: case.expected_decision,
            predicate=lambda case, evaluation, run: not evaluation.decision_correct,
            actual=lambda case, evaluation, run: str(run.decision),
        ),
        _GalleryRule(
            pipeline="baseline",
            reason="naive baseline approved a hard stop",
            expected="decline or hold",
            predicate=lambda case, evaluation, run: evaluation.false_approve,
            actual=lambda case, evaluation, run: str(run.decision),
        ),
    )


def build_failure_gallery(
    cases: list[EvalCase],
    baseline_runs: dict[str, PipelineRun],
    fixed_runs: dict[str, PipelineRun],
) -> list[FailureItem]:
    """Collect high-signal failures from a data-driven rule table."""

    items: list[FailureItem] = []
    for case in cases:
        runs = {
            "baseline": baseline_runs[case.case_id],
            "fixed": fixed_runs[case.case_id],
        }
        for rule in _gallery_rules():
            run = runs[rule.pipeline]
            evaluation = evaluate_case(case, run)
            if not rule.predicate(case, evaluation, run):
                continue
            expected = rule.expected(case) if callable(rule.expected) else rule.expected
            items.append(
                FailureItem(
                    pipeline=rule.pipeline,
                    case_id=case.case_id,
                    split=case.split,
                    category=case.category,
                    reason=rule.reason,
                    expected=expected,
                    actual=rule.actual(case, evaluation, run),
                )
            )
    return items


def _release_gate(gates: list[HardGate], *, applicable: bool) -> ReleaseGate:
    if any(not gate.passed for gate in gates):
        return "failed"
    return "passed" if applicable else "not_applicable"


def build_report(
    *,
    dataset: EvalDataset,
    provider: ProviderInfo,
    pricing: PricingInfo,
    eval_metrics: SplitMetrics,
    held_out_metrics: SplitMetrics,
    gates: list[HardGate],
    gate_applicable: bool,
    approval_probes: list[ApprovalProbeInfo],
    cases: list[CaseComparison],
    failure_gallery: list[FailureItem],
    eval_cases_run: int,
    held_out_cases_run: int,
    subset: bool,
    generated_at: datetime | None = None,
    caveats: list[str] | None = None,
) -> EvalReport:
    baseline = eval_metrics.baseline
    default_caveats = [
        "The corpus is synthetic and watermarked; no real carrier, person or bank data is used.",
        (
            "Baseline prompt decisions: "
            f"{baseline.prompt_decisions} parsed from the provider; "
            f"{baseline.fallback_decisions} fell back to a default approve."
        ),
        (
            "Token counts are character-based estimates because the provider contract "
            "has no usage field."
        ),
        (
            "The baseline has no citations, validators, approval or audit, and its direct "
            "store has no idempotency, so its retry deliberately duplicates the write."
        ),
        (
            "The fixed pipeline's duplicate-write check replays the identical decision "
            "write with the same key; the graph itself is checkpointed after the first commit."
        ),
        "Held-out results are reported separately and are not part of the release hard gates.",
        "No LLM judge is used; all metrics are deterministic against the manifest "
        "and generated ground truth.",
    ]
    if pricing.mode == "free":
        default_caveats.append(
            "The offline FakeProvider/free model has no dollar price; token costs are "
            "reported as $0.00 and token counts remain estimates."
        )
    elif pricing.mode == "unpriced":
        default_caveats.append(
            "No input/output prices were configured, so token cost is reported as n/a; "
            "token counts are still reported."
        )
    return EvalReport(
        generated_at=generated_at or datetime.now(UTC),
        provider=provider,
        pricing=pricing,
        dataset=DatasetInfo(
            manifest_path=str(dataset.manifest_path),
            manifest_hash=dataset.manifest_hash,
            corpus_dir=str(dataset.corpus_dir),
            as_of=dataset.manifest.as_of.isoformat(),
            eval_cases=len(dataset.split("eval")),
            held_out_cases=len(dataset.split("held_out")),
            eval_cases_run=eval_cases_run,
            held_out_cases_run=held_out_cases_run,
            carrier_count=dataset.carrier_count,
            subset=subset,
        ),
        eval=eval_metrics,
        held_out=held_out_metrics,
        gates=gates,
        gate_applicable=gate_applicable,
        release_gate=_release_gate(gates, applicable=gate_applicable),
        approval_probes=approval_probes,
        cases=cases,
        failure_gallery=failure_gallery,
        caveats=caveats or default_caveats,
    )


def _fmt_metric(value: float | None, unit: MetricUnit) -> str:
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


def _field_denominator_table(split: SplitMetrics) -> list[str]:
    lines = [
        "Field F1 denominators (oracle fields, not cases):",
        "",
        "| Group | Fields (baseline / fixed) | Cases (baseline / fixed) |",
        "|---|---:|---:|",
    ]
    for name, label in (("field_f1_clean", "clean"), ("field_f1_noisy", "noisy")):
        pair = split.metrics[name]
        lines.append(
            f"| {label} | {pair.baseline.n} / {pair.fixed.n} | "
            f"{pair.baseline.case_count or 0} / {pair.fixed.case_count or 0} |"
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
        "| Tokens | Tool calls | Total latency | Est. cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| baseline | {split.baseline.cases} | {split.baseline.decisions} | "
        f"{split.baseline.approvals} | {split.baseline.write_commits} | "
        f"{split.baseline.duplicate_writes} | {split.baseline.total_tokens} | "
        f"{split.baseline.tool_calls_attempted} | "
        f"{split.baseline.duration_ms_total:.1f} ms | "
        f"{_fmt_metric(split.baseline.estimated_usd, 'usd')} |",
        f"| fixed | {split.fixed.cases} | {split.fixed.decisions} | "
        f"{split.fixed.approvals} | {split.fixed.write_commits} | "
        f"{split.fixed.duplicate_writes} | {split.fixed.total_tokens} | "
        f"{split.fixed.tool_calls_attempted} | "
        f"{split.fixed.duration_ms_total:.1f} ms | "
        f"{_fmt_metric(split.fixed.estimated_usd, 'usd')} |",
    ]


def _failure_section(title: str, items: list[FailureItem]) -> list[str]:
    lines = [f"### {title}", ""]
    if not items:
        return [*lines, "None.", ""]
    for item in items:
        held_out = " (held-out)" if item.split == "held_out" else ""
        lines.append(
            f"- `{item.case_id}`{held_out} ({item.category}): **{item.reason}** — "
            f"expected {item.expected}; got {item.actual}"
        )
    lines.append("")
    return lines


def render_markdown(report: EvalReport) -> str:
    gate_label = report.release_gate.upper()
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
        f"| Pricing | {report.pricing.mode} ({report.pricing.note}) |",
        f"| Manifest | `{report.dataset.manifest_hash}` |",
        f"| Dataset | {report.dataset.eval_cases} eval + "
        f"{report.dataset.held_out_cases} held out |",
        f"| Cases run | {report.dataset.eval_cases_run} eval + "
        f"{report.dataset.held_out_cases_run} held out"
        f"{' (subset run)' if report.dataset.subset else ''} |",
        f"| Gate applicable | {'yes' if report.gate_applicable else 'no'} |",
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
        *_field_denominator_table(report.eval),
        "",
        *(
            [
                "> **Subset run:** these metrics cover only the selected cases; "
                "the release gate and audit completeness are not a release claim.",
                "",
            ]
            if report.dataset.subset
            else []
        ),
        "## Hard gates",
        "",
        "| Gate | Requirement | Baseline | Fixed | Status |",
        "|---|---|---:|---:|---|",
    ]
    for gate in report.gates:
        baseline = "yes" if gate.baseline_passed else "no"
        fixed = "yes" if gate.passed else "no"
        status = "PASS" if gate.passed else "FAIL"
        if not report.gate_applicable:
            status = "N/A (subset)"
        lines.append(
            f"| `{gate.gate_id}` | {gate.comparator} {gate.threshold:.2f} | "
            f"{gate.baseline_value!r} ({baseline}) | {gate.value!r} ({fixed}) | {status} |"
        )
    if report.approval_probes:
        blocked = sum(1 for probe in report.approval_probes if probe.blocked)
        lines.extend(
            [
                "",
                f"Approval probes blocked: {blocked}/{len(report.approval_probes)} "
                "(blank, expired, mismatched, wrong payload, reused).",
            ]
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
    for pipeline in ("fixed", "baseline"):
        for split, label in (("eval", "eval"), ("held_out", "held out")):
            items = [
                item
                for item in report.failure_gallery
                if item.pipeline == pipeline and item.split == split
            ]
            title = f"{pipeline.capitalize()} pipeline — {label}"
            lines.extend(_failure_section(title, items))
    lines.extend(["## Caveats", ""])
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
    """Only a full, passing release-gate run exits zero."""

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
