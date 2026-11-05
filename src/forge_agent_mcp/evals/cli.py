"""Command-line interface for the evaluation harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

from ..config import Settings
from ..providers import FakeProvider, build_provider
from ..providers.base import ChatProvider
from .baseline import BASELINE_DEFAULT_RESPONSE
from .report import report_exit_code
from .runner import EvalConfig, EvalRunError, run_evals

DEFAULT_MANIFEST = Path("corpus/manifest.json")
DEFAULT_CORPUS = Path("data/corpus")
DEFAULT_OUT = Path("reports")
ProviderLabel = Literal["fake", "orcarouter", "lfm3"]


def build_provider_for_label(label: ProviderLabel) -> ChatProvider:
    if label == "fake":
        return FakeProvider(text=BASELINE_DEFAULT_RESPONSE)
    settings = Settings(model_provider=label)
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
        help="run only this case (repeatable); requires --allow-subset",
    )
    parser.add_argument(
        "--allow-subset",
        action="store_true",
        help="allow a subset run; its release gate is reported as not applicable",
    )
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--input-price-per-1k", type=float, default=None)
    parser.add_argument("--output-price-per-1k", type=float, default=None)
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
                allow_subset=args.allow_subset,
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
    print(f"Pricing:     {report.pricing.mode} ({report.pricing.note})")
    for gate in report.gates:
        status = "PASS" if gate.passed else "FAIL"
        if not report.gate_applicable:
            status = "N/A (subset)"
        print(
            f"  [{status}] {gate.gate_id}: fixed={gate.value!r} "
            f"required {gate.comparator} {gate.threshold}"
        )
    agreement = report.eval.metrics["decision_agreement"]
    print(
        f"Decision agreement: baseline={agreement.baseline.value!r} fixed={agreement.fixed.value!r}"
    )
    print(
        f"Release gate: {report.release_gate.upper()} "
        f"({report.dataset.eval_cases_run} eval + "
        f"{report.dataset.held_out_cases_run} held out)"
    )
    return report_exit_code(report)


__all__ = [
    "DEFAULT_CORPUS",
    "DEFAULT_MANIFEST",
    "DEFAULT_OUT",
    "build_provider_for_label",
    "main",
]
