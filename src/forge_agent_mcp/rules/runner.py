"""Corpus/extraction adapter around the pure rule engine.

The engine itself performs no I/O. The CLI and future agent runner use this module
when they need a complete review from a generated corpus case.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ..extraction.extractor import extract_case
from .corpus import RuleEngineError, load_case_packet
from .engine import review
from .models import ReviewResult, RuleErrorCode
from .policy import DEMO_POLICY, RulePolicy


def review_case(
    case_id: str,
    corpus_dir: str | Path,
    *,
    policy: RulePolicy = DEMO_POLICY,
) -> ReviewResult:
    """Extract a case deterministically, then run the pure rule engine."""

    try:
        packet = load_case_packet(case_id, corpus_dir)
    except RuleEngineError as exc:
        return ReviewResult(ok=False, error_code=exc.code, message=str(exc))

    extracted = extract_case(case_id, corpus_dir)
    if not extracted.ok or extracted.data is None:
        code: RuleErrorCode = "extraction_failed"
        if extracted.error_code in ("unknown_case", "corpus_not_found", "corpus_invalid"):
            code = cast(RuleErrorCode, extracted.error_code)
        return ReviewResult(
            ok=False,
            error_code=code,
            message=extracted.message or "extraction failed",
        )
    return ReviewResult(ok=True, data=review(extracted.data, packet, policy=policy))


__all__ = ["review_case"]
