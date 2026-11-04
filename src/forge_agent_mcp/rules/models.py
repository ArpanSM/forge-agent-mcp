"""Public rule-engine contracts: evidence, flags, recommendations and reviews."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from ..corpus.vocab import (
    CaseType,
    Decision,
    Recommendation,
    Severity,
    StrictModel,
)

FlagCode = Literal[
    "watchlist_hit",
    "authority_inactive",
    "identity_mismatch",
    "insurance_expired",
    "insurance_below_minimum",
    "duplicate_invoice",
    "bank_account_changed",
    "bank_account_name_mismatch",
    "invoice_arithmetic_mismatch",
    "pod_mismatch",
    "missing_document",
    "document_instruction_injection",
    "authority_young",
    "round_dollar_invoice",
    "altered_metadata",
    "missing_optional_field",
    "formatting_inconsistency",
]
RuleErrorCode = Literal[
    "unknown_case",
    "corpus_not_found",
    "corpus_invalid",
    "extraction_failed",
]


class Evidence(StrictModel):
    """Where a flag came from: a document span, a structured record or a gap."""

    document_id: str | None = None
    document_type: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    quote: str | None = None
    record_id: str | None = None
    record_path: str | None = None
    missing_document_type: str | None = None
    missing_field: str | None = None


class RuleFlag(StrictModel):
    code: FlagCode
    severity: Severity
    rule_id: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    details: str = Field(min_length=1)
    evidence: Evidence


class RecommendationResult(StrictModel):
    recommendation: Recommendation
    decision: Decision
    rationale: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)


class CaseReview(StrictModel):
    case_id: str
    case_type: CaseType
    as_of: date | None = None
    flags: list[RuleFlag] = Field(default_factory=list)
    recommendation: RecommendationResult
    summary: str = Field(min_length=1)


class ReviewResult(StrictModel):
    """Result envelope for the case-review operation."""

    ok: bool
    error_code: RuleErrorCode | None = None
    message: str | None = None
    data: CaseReview | None = None


__all__ = [
    "CaseReview",
    "Evidence",
    "FlagCode",
    "RecommendationResult",
    "ReviewResult",
    "RuleErrorCode",
    "RuleFlag",
]
