"""Orchestrate the eval run: dataset, both pipelines, metrics and reports."""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..config import Settings
from ..providers import FakeProvider, build_provider
from ..providers.base import ChatProvider
from .baseline import BaselinePipeline
from .dataset import EvalDataset, load_eval_dataset
from .fixed import FixedPipeline
from .metrics import evaluate_gates
from .models import EvalCase, EvalReport, PipelineRun, ProviderInfo
from .report import (
    build_case_comparisons,
    build_failure_gallery,
    build_report,
    build_split_metrics,
    report_exit_code,
    write_report,
)

DEFAULT_MANIFEST = Path("corpus/manifest.json")
DEFAULT_CORPUS = Path("data/corpus")
DEFAULT_OUT = Path("reports")


class EvalRunError(RuntimeError):
    """The eval could not start or collect a complete run."""


@dataclass(frozen=True)
class EvalConfig:
    manifest_path: Path = DEFAULT_MANIFEST
    corpus_dir: Path = DEFAULT_CORPUS
    out_dir: Path = DEFAULT_OUT
    case_ids: tuple[str, ...] | None = None
    seed: int = 12345
    input_price_per_1k: float = 0.0
    output_price_per_1k: float = 0.0
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
    selected = [case for case in dataset.cases if case.case_id in set(case_ids)]
    missing = sorted(set(case_ids) - {case.case_id for case in selected})
    if missing:
        raise EvalRunError(f"unknown case id(s) for the eval dataset: {missing}")
    return selected


def run_evals(config: EvalConfig, *, provider: ChatProvider | None = None) -> EvalReport:
    """Run both pipelines on the selected fixed cases and build the report."""

    dataset = load_eval_dataset(config.manifest_path, config.corpus_dir)
    cases = _selected_cases(dataset, config.case_ids)
    if not cases:
        raise EvalRunError("the selected eval set is empty")
    out_dir = Path(config.out_dir)
    work_dir = out_dir / "work"
    _reset_work_dir(work_dir)
    model = provider or FakeProvider()
    mode = "offline" if model.name == "fake" else "live"
    provider_info = _provider_info(model, mode=mode)

    baseline = BaselinePipeline(
        dataset,
        provider=model,
        db_path=work_dir / "baseline.db",
    )
    try:
        fixed = FixedPipeline(
            dataset,
            provider=model,
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

    eval_cases = [case for case in cases if case.split == "eval"]
    held_out_cases = [case for case in cases if case.split == "held_out"]
    eval_metrics = build_split_metrics(
        split="eval",
        cases=eval_cases,
        baseline_runs=baseline_runs,
        fixed_runs=fixed_runs,
        seed=config.seed,
        input_price_per_1k=config.input_price_per_1k,
        output_price_per_1k=config.output_price_per_1k,
    )
    held_out_metrics = build_split_metrics(
        split="held_out",
        cases=held_out_cases,
        baseline_runs=baseline_runs,
        fixed_runs=fixed_runs,
        seed=config.seed,
        input_price_per_1k=config.input_price_per_1k,
        output_price_per_1k=config.output_price_per_1k,
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
        eval_metrics=eval_metrics,
        held_out_metrics=held_out_metrics,
        gates=gates,
        cases=comparisons,
        failure_gallery=failures,
    )
    if config.write_reports:
        write_report(report, out_dir)
    return report


def build_provider_for_label(label: str) -> ChatProvider:
    if label == "fake":
        return FakeProvider()
    if label not in {"orcarouter", "lfm3"}:
        raise EvalRunError(f"unknown provider {label!r}; use fake, orcarouter or lfm3")
    settings = Settings(model_provider=label)  # type: ignore[arg-type]
    if label == "orcarouter":
        key = settings.orcarouter_api_key
        if key is None or not key.get_secret_value().strip():
            raise EvalRunError(
                "ORCAROUTER_API_KEY is not configured; `make eval-live` needs a live key"
            )
    return build_provider(settings)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m forge_agent_mcp.evals",
        description="Run the naive baseline and the fixed pipeline over the synthetic eval corpus.",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--provider", choices=["fake", "orcarouter", "lfm3"], default="fake")
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="run only this case (repeatable); the full 12+4 run is the release gate",
    )
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--input-price-per-1k", type=float, default=0.0)
    parser.add_argument("--output-price-per-1k", type=float, default=0.0)
    parser.add_argument("--no-write", action="store_true", help="run without writing report files")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        provider = build_provider_for_label(args.provider)
        report = run_evals(
            EvalConfig(
                manifest_path=args.manifest,
                corpus_dir=args.corpus_dir,
                out_dir=args.out,
                case_ids=tuple(args.case_id) if args.case_id else None,
                seed=args.seed,
                input_price_per_1k=args.input_price_per_1k,
                output_price_per_1k=args.output_price_per_1k,
                write_reports=not args.no_write,
            ),
            provider=provider,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must return a useful non-zero status
        print(f"eval failed: {exc}", file=sys.stderr)
        return 2

    print(f"Eval report: {args.out / 'eval-report.json'}")
    print(f"Markdown:    {args.out / 'eval-report.md'}")
    print(f"Provider:    {report.provider.name}/{report.provider.model} ({report.provider.mode})")
    for gate in report.gates:
        status = "PASS" if gate.passed else "FAIL"
        print(
            f"  [{status}] {gate.gate_id}: fixed={gate.value!r} "
            f"required {gate.comparator} {gate.threshold}"
        )
    agreement = report.eval.metrics["decision_agreement"]
    print(
        f"Decision agreement: baseline={agreement.baseline.value!r} fixed={agreement.fixed.value!r}"
    )
    print(f"Release gate: {report.release_gate.upper()}")
    return report_exit_code(report)


__all__ = [
    "EvalConfig",
    "EvalRunError",
    "build_provider_for_label",
    "main",
    "run_evals",
]
