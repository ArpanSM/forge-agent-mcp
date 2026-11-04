"""Offline CLI for ``make review``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import review_case
from .models import CaseReview, Evidence

DEFAULT_CORPUS = Path("data") / "corpus"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m forge_agent_mcp.rules",
        description="Review a synthetic carrier packet with deterministic underwriting rules.",
    )
    parser.add_argument("--case", required=True, help="case id from data/corpus/corpus.json")
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help=f"generated corpus directory (default: {DEFAULT_CORPUS})",
    )
    return parser


def _format_evidence(evidence: Evidence) -> str:
    parts: list[str] = []
    if evidence.document_id:
        parts.append(f"document={evidence.document_id}")
    if evidence.document_type:
        parts.append(f"type={evidence.document_type}")
    if evidence.page is not None:
        parts.append(f"page={evidence.page}")
    if evidence.bbox is not None:
        parts.append(f"bbox={list(evidence.bbox)}")
    if evidence.quote is not None:
        parts.append(f"quote={json.dumps(evidence.quote)}")
    if evidence.record_id:
        parts.append(f"record={evidence.record_id}")
    if evidence.record_path:
        parts.append(f"path={evidence.record_path}")
    if evidence.missing_document_type:
        parts.append(f"missing_document={evidence.missing_document_type}")
    if evidence.missing_field:
        parts.append(f"missing_field={evidence.missing_field}")
    return ", ".join(parts) or "no citation"


def _print_review(review: CaseReview) -> None:
    as_of = review.as_of.isoformat() if review.as_of else "unknown"
    print(f"Case: {review.case_id} ({review.case_type}) as of {as_of}")
    if not review.flags:
        print("Flags: none")
    else:
        print("Flags:")
        for flag in review.flags:
            print(f"  [{flag.severity}] {flag.code} ({flag.rule_id})")
            print(f"    {flag.details}")
            print(f"    evidence: {_format_evidence(flag.evidence)}")

    recommendation = review.recommendation
    print(f"Recommendation: {recommendation.recommendation}")
    print(f"Decision: {recommendation.decision}")
    print(f"Rationale: {recommendation.rationale}")
    if recommendation.conditions:
        print("Conditions:")
        for condition in recommendation.conditions:
            print(f"  - {condition}")
    else:
        print("Conditions: none")
    if recommendation.evidence_gaps:
        print("Evidence gaps:")
        for gap in recommendation.evidence_gaps:
            print(f"  - {gap}")
    else:
        print("Evidence gaps: none")
    print(f"Summary: {review.summary}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = review_case(args.case, args.corpus)
    if not result.ok or result.data is None:
        print(f"error: {result.message or 'review failed'}", file=sys.stderr)
        return 2
    _print_review(result.data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
