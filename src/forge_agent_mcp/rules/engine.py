"""Pure validation and recommendation functions for one case review."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..extraction.extractor import extract_case
from ..extraction.models import CaseExtraction
from .catalog import FLAG_ORDER, sort_flags
from .conditions import condition_for
from .corpus import CasePacket, RuleEngineError, load_case_packet
from .models import CaseReview, RecommendationResult, ReviewResult, RuleFlag
from .validators import (
    find_missing_documents,
    validate_authority,
    validate_bank,
    validate_duplicates,
    validate_identity,
    validate_insurance,
    validate_injection,
    validate_invoice_arithmetic,
    validate_optional_fields,
    validate_pod,
    validate_round_dollar,
    validate_required_documents,
    validate_watchlist,
)
from .values import Documents


def _as_of(extraction: CaseExtraction, packet: CasePacket) -> date:
    if extraction.as_of:
        return date.fromisoformat(extraction.as_of)
    return packet.as_of


def validate(extraction: CaseExtraction, packet: CasePacket) -> list[RuleFlag]:
    """Run every deterministic validator over one extraction report and packet."""

    if extraction.case_id != packet.case_id:
        raise ValueError(
            f"extraction case {extraction.case_id!r} does not match packet {packet.case_id!r}"
        )
    documents = Documents.from_extraction(extraction)
    as_of = _as_of(extraction, packet)
    flags = [
        *validate_watchlist(packet),
        *validate_authority(documents, as_of),
        *validate_identity(documents),
        *validate_insurance(documents, as_of),
        *validate_bank(documents, as_of),
        *validate_duplicates(documents, packet),
        *validate_invoice_arithmetic(documents),
        *validate_pod(documents),
        *validate_round_dollar(documents),
        *validate_optional_fields(documents),
        *validate_injection(extraction),
        *validate_required_documents(extraction, packet),
    ]
    return sort_flags(flags)


def _ordered_conditions(flags: list[RuleFlag]) -> list[str]:
    ordered = sorted(
        flags,
        key=lambda flag: (
            0 if flag.code == "missing_document" else 1,
            FLAG_ORDER.get(flag.code, len(FLAG_ORDER)),
        ),
    )
    conditions: list[str] = []
    for flag in ordered:
        condition = condition_for(flag)
        if condition is not None and condition not in conditions:
            conditions.append(condition)
    return conditions


def recommend(
    flags: list[RuleFlag],
    *,
    evidence_gaps: list[str] | None = None,
) -> RecommendationResult:
    """Map validated flags onto the fixed underwriting decision rubric."""

    gaps = list(evidence_gaps or [])
    hard_stops = [flag for flag in flags if flag.severity == "hard_stop"]
    if hard_stops:
        escalate = any(flag.code == "watchlist_hit" for flag in hard_stops)
        codes = ", ".join(flag.code for flag in hard_stops)
        if escalate:
            rationale = f"A synthetic watchlist hit is a hard stop ({codes}); escalate for review."
        else:
            rationale = f"The packet has {len(hard_stops)} hard-stop flag(s) ({codes}); decline."
        return RecommendationResult(
            recommendation="escalate" if escalate else "decline",
            decision="decline",
            rationale=rationale,
            conditions=[],
            evidence_gaps=gaps,
        )

    injections = [flag for flag in flags if flag.code == "document_instruction_injection"]
    if injections:
        return RecommendationResult(
            recommendation="hold",
            decision="request_documents",
            rationale=(
                "Document-embedded instructions were detected and ignored; "
                "hold the packet for manual review."
            ),
            conditions=_ordered_conditions(flags),
            evidence_gaps=gaps,
        )

    missing = [flag for flag in flags if flag.rule_id == "documents.required"]
    if missing:
        return RecommendationResult(
            recommendation="request_documents",
            decision="request_documents",
            rationale="Required packet documents are missing; request them before any decision.",
            conditions=_ordered_conditions(flags),
            evidence_gaps=gaps,
        )

    highs = [flag for flag in flags if flag.severity == "high"]
    if len(highs) >= 2:
        return RecommendationResult(
            recommendation="hold",
            decision="request_documents",
            rationale=f"{len(highs)} high-severity flags require a hold for manual review.",
            conditions=_ordered_conditions(flags),
            evidence_gaps=gaps,
        )
    if len(highs) == 1:
        return RecommendationResult(
            recommendation="conditional_approve",
            decision="conditional_approve",
            rationale="One high-severity flag requires a condition before approval.",
            conditions=_ordered_conditions(flags),
            evidence_gaps=gaps,
        )

    mediums = [flag for flag in flags if flag.severity == "medium"]
    if mediums:
        return RecommendationResult(
            recommendation="conditional_approve",
            decision="conditional_approve",
            rationale="Only medium-severity flags remain; approve with conditions.",
            conditions=_ordered_conditions(flags),
            evidence_gaps=gaps,
        )

    lows = [flag for flag in flags if flag.severity == "low"]
    if lows:
        return RecommendationResult(
            recommendation="approve",
            decision="approve",
            rationale="Only low-severity clarifications remain; approve with clarification.",
            conditions=_ordered_conditions(flags),
            evidence_gaps=gaps,
        )

    return RecommendationResult(
        recommendation="approve",
        decision="approve",
        rationale="No rule flags were found; the packet is clean.",
        conditions=[],
        evidence_gaps=gaps,
    )


def _evidence_gaps(extraction: CaseExtraction, packet: CasePacket) -> list[str]:
    gaps: list[str] = []
    for missing in find_missing_documents(extraction, packet):
        if missing.details not in gaps:
            gaps.append(missing.details)
    for issue in extraction.issues:
        gap = f"{issue.document_id or 'case'}: {issue.message}"
        if gap not in gaps:
            gaps.append(gap)
    return gaps


def _summary(case_id: str, flags: list[RuleFlag], recommendation: RecommendationResult) -> str:
    if not flags:
        finding = "No rule flags."
    else:
        finding = f"{len(flags)} rule flag(s): " + ", ".join(flag.code for flag in flags) + "."
    return (
        f"{case_id}: {recommendation.recommendation} "
        f"(decision {recommendation.decision}). {finding} {recommendation.rationale}"
    ).strip()


def review(extraction: CaseExtraction, packet: CasePacket) -> CaseReview:
    """Validate, recommend and summarise a case; never calls a model or network."""

    flags = validate(extraction, packet)
    recommendation = recommend(flags, evidence_gaps=_evidence_gaps(extraction, packet))
    return CaseReview(
        case_id=extraction.case_id,
        case_type=extraction.case_type,
        as_of=_as_of(extraction, packet),
        flags=flags,
        recommendation=recommendation,
        summary=_summary(extraction.case_id, flags, recommendation),
    )


def review_case(case_id: str, corpus_dir: str | Path) -> ReviewResult:
    """Offline adapter: extract the case deterministically, then review it."""

    try:
        packet = load_case_packet(case_id, corpus_dir)
    except RuleEngineError as exc:
        return ReviewResult(ok=False, error_code=exc.code, message=str(exc))

    extracted = extract_case(case_id, corpus_dir)
    if not extracted.ok or extracted.data is None:
        return ReviewResult(
            ok=False,
            error_code=extracted.error_code or "extraction_failed",
            message=extracted.message or "extraction failed",
        )
    return ReviewResult(ok=True, data=review(extracted.data, packet))


__all__ = ["recommend", "review", "review_case", "validate"]
