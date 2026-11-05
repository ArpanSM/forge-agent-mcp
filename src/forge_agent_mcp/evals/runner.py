"""Orchestrate the eval run: dataset, both pipelines, metrics and reports."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..providers import FakeProvider
from ..providers.base import ChatProvider
from .baseline import BASELINE_DEFAULT_RESPONSE, BaselinePipeline
from .dataset import EvalDataset, load_eval_dataset
from .fixed import FixedPipeline
from .metrics import evaluate_gates
from .models import (
    ApprovalProbeInfo,
    EvalCase,
    EvalReport,
    PipelineRun,
    PricingInfo,
    ProviderInfo,
)
from .report import (
    build_case_comparisons,
    build_failure_gallery,
    build_report,
    build_split_metrics,
    write_report,
)


class EvalRunError(RuntimeError):
    """The eval could not start or collect a complete run."""


@dataclass(frozen=True)
class EvalConfig:
    manifest_path: Path
    corpus_dir: Path
    out_dir: Path
    case_ids: tuple[str, ...] | None = None
    allow_subset: bool = False
    seed: int = 12345
    input_price_per_1k: float | None = None
    output_price_per_1k: float | None = None
    write_reports: bool = True


def _provider_info(provider: ChatProvider, *, mode: Literal["offline", "live"]) -> ProviderInfo:
    return ProviderInfo(
        name=provider.name,
        model=provider.model,
        mode=mode,
        details=(
            "FakeProvider with deterministic extraction fallback"
            if mode == "offline"
            else "OpenAI-compatible provider over the configured endpoint"
        ),
    )


def _reset_work_dir(work_dir: Path) -> None:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)


def _selected_cases(dataset: EvalDataset, case_ids: tuple[str, ...] | None) -> list[EvalCase]:
    if not case_ids:
        return list(dataset.cases)
    requested = set(case_ids)
    selected = [case for case in dataset.cases if case.case_id in requested]
    missing = sorted(requested - {case.case_id for case in selected})
    if missing:
        raise EvalRunError(f"unknown case id(s) for the eval dataset: {missing}")
    return selected


def _pricing(
    model: ChatProvider,
    config: EvalConfig,
) -> tuple[PricingInfo, float | None, float | None]:
    if config.input_price_per_1k is not None and config.output_price_per_1k is not None:
        return (
            PricingInfo(
                mode="priced",
                input_price_per_1k=config.input_price_per_1k,
                output_price_per_1k=config.output_price_per_1k,
                note="Estimated token cost from the supplied input/output prices.",
            ),
            config.input_price_per_1k,
            config.output_price_per_1k,
        )
    if config.input_price_per_1k is not None or config.output_price_per_1k is not None:
        raise EvalRunError(
            "provide both --input-price-per-1k and --output-price-per-1k, or neither"
        )
    if model.name == "fake":
        note = (
            "FakeProvider is free; token cost is reported as $0.00 and token counts are estimates."
        )
        return (
            PricingInfo(
                mode="free",
                input_price_per_1k=0.0,
                output_price_per_1k=0.0,
                note=note,
            ),
            0.0,
            0.0,
        )
    return (
        PricingInfo(
            mode="unpriced",
            note="No input/output prices configured; token cost is not claimed.",
        ),
        None,
        None,
    )


def run_evals(config: EvalConfig, *, provider: ChatProvider | None = None) -> EvalReport:
    """Run both pipelines on the selected fixed cases and build the report."""

    dataset = load_eval_dataset(config.manifest_path, config.corpus_dir)
    all_case_ids = {case.case_id for case in dataset.cases}
    cases = _selected_cases(dataset, config.case_ids)
    if not cases:
        raise EvalRunError("the selected eval set is empty")
    subset = {case.case_id for case in cases} != all_case_ids
    if subset and not config.allow_subset:
        raise EvalRunError(
            "a subset run requires allow_subset=True; the release gate needs all "
            "12 eval + 4 held-out cases"
        )
    out_dir = Path(config.out_dir)
    work_dir = out_dir / "work"
    _reset_work_dir(work_dir)
    if provider is None:
        provider = FakeProvider(text=BASELINE_DEFAULT_RESPONSE)
    mode = "offline" if provider.name == "fake" else "live"
    provider_info = _provider_info(provider, mode=mode)
    pricing_info, input_price, output_price = _pricing(provider, config)

    baseline = BaselinePipeline(
        dataset,
        provider=provider,
        db_path=work_dir / "baseline.db",
    )
    try:
        fixed = FixedPipeline(
            dataset,
            provider=provider,
            work_dir=work_dir / "fixed",
        )
    except Exception:
        baseline.close()
        raise
    baseline_runs: dict[str, PipelineRun] = {}
    fixed_runs: dict[str, PipelineRun] = {}
    try:
        for case in cases:
            baseline_runs[case.case_id] = baseline.run_case(case.case_id)
            fixed_runs[case.case_id] = fixed.run_case(case.case_id)
    finally:
        baseline.close()
        fixed.close()
    approval_probes = [
        ApprovalProbeInfo(
            name=probe.name,
            expected_code=probe.expected_code,
            actual_code=probe.actual_code,
            blocked=probe.blocked,
        )
        for probe in fixed.bypass_probes
    ]

    eval_cases = [case for case in cases if case.split == "eval"]
    held_out_cases = [case for case in cases if case.split == "held_out"]
    eval_metrics = build_split_metrics(
        split="eval",
        cases=eval_cases,
        baseline_runs=baseline_runs,
        fixed_runs=fixed_runs,
        seed=config.seed,
        input_price_per_1k=input_price,
        output_price_per_1k=output_price,
        subset=subset,
    )
    held_out_metrics = build_split_metrics(
        split="held_out",
        cases=held_out_cases,
        baseline_runs=baseline_runs,
        fixed_runs=fixed_runs,
        seed=config.seed,
        input_price_per_1k=input_price,
        output_price_per_1k=output_price,
        subset=subset,
    )
    gates = evaluate_gates(
        {name: pair.fixed for name, pair in eval_metrics.metrics.items()},
        {name: pair.baseline for name, pair in eval_metrics.metrics.items()},
    )
    comparisons = build_case_comparisons(cases, baseline_runs, fixed_runs)
    failures = build_failure_gallery(cases, baseline_runs, fixed_runs)
    report = build_report(
        dataset=dataset,
        provider=provider_info,
        pricing=pricing_info,
        eval_metrics=eval_metrics,
        held_out_metrics=held_out_metrics,
        gates=gates,
        gate_applicable=not subset,
        approval_probes=approval_probes,
        cases=comparisons,
        failure_gallery=failures,
        eval_cases_run=len(eval_cases),
        held_out_cases_run=len(held_out_cases),
        subset=subset,
    )
    if config.write_reports:
        write_report(report, out_dir)
    return report


__all__ = ["EvalConfig", "EvalRunError", "run_evals"]
