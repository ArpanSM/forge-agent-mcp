"""Command-line entry point for offline and provider-backed extraction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import Settings
from ..providers import build_provider
from ..providers.base import ProviderError
from .extractor import extract_case
from .models import CaseExtraction

DEFAULT_CORPUS = Path("data") / "corpus"
DEFAULT_REPORT_DIR = Path("data") / "extractions"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m forge_agent_mcp.extraction",
        description="Classify a synthetic carrier packet and extract cited fields.",
    )
    parser.add_argument("--case", required=True, help="case id from data/corpus/corpus.json")
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help=f"generated corpus directory (default: {DEFAULT_CORPUS})",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=f"report path (default: {DEFAULT_REPORT_DIR}/<case>.json)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use the deterministic template parser and never call a model",
    )
    return parser


def _select_provider(offline: bool):
    if offline:
        return None
    return build_provider(Settings())


def _report_path(case_id: str, out: str | None) -> Path:
    return Path(out) if out else DEFAULT_REPORT_DIR / f"{case_id}.json"


def _print_report(report: CaseExtraction, out: Path) -> None:
    print(
        f"Case: {report.case_id} ({report.case_type}) "
        f"status={report.status} provider={report.provider}"
    )
    for document in report.documents:
        classification = document.classification
        print(
            f"- {classification.document_id} {classification.document_type} "
            f"({classification.confidence:.2f}) classifier={classification.classifier} "
            f"status={document.status}"
        )
        if document.fallback_used:
            print("  fallback: deterministic template parser used")
        if document.error:
            print(f"  error: {document.error}")
        for key, value in document.fields.items():
            citation = value.citation
            print(f"  {key} = {json.dumps(value.value)} confidence={value.confidence:.2f}")
            print(
                f"    cite: {citation.document_id} page {citation.page} "
                f"bbox={list(citation.bbox)} quote={json.dumps(citation.quote)}"
            )
    if report.missing_documents:
        print("Missing documents: " + ", ".join(report.missing_documents))
    for issue in report.issues:
        print(f"Issue [{issue.code}] {issue.document_id}: {issue.message}")
    print(f"Report: {out}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out = _report_path(args.case, args.out)
    try:
        provider = _select_provider(args.offline)
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = extract_case(args.case, args.corpus, provider=provider)
    if not result.ok or result.data is None:
        print(f"error: {result.message or 'extraction failed'}", file=sys.stderr)
        return 2

    report = result.data
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _print_report(report, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
