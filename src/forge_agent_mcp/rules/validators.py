"""Deterministic validators over extracted fields and structured records."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from ..corpus.models import WatchlistRecord
from ..extraction.models import ExtractedValue
from .catalog import make_flag
from .corpus import CasePacket
from .models import Evidence, RuleFlag
from .values import (
    Documents,
    evidence_for,
    format_money,
    normalize_identifier,
    normalize_name,
    parse_date,
    parse_money,
)

AUTO_LIABILITY_MINIMUM = Decimal("1000000.00")
CARGO_MINIMUM = Decimal("100000.00")


def _compare(
    documents: Documents,
    *,
    left_type: str,
    left_key: str,
    right_type: str,
    right_key: str,
    label: str,
    right_label: str,
    normalizer: Callable[[str], str],
) -> tuple[str, Evidence] | None:
    left: ExtractedValue | None = documents.field(left_type, left_key)
    right: ExtractedValue | None = documents.field(right_type, right_key)
    if left is None or right is None:
        return None
    if normalizer(left.value) == normalizer(right.value):
        return None
    detail = (
        f"{label.capitalize()} {right.value!r} on the {right_label} differs from "
        f"{left.value!r} on the application."
    )
    return detail, evidence_for(right, right_type)


def validate_identity(documents: Documents) -> list[RuleFlag]:
    checks = (
        _compare(
            documents,
            left_type="application_kyc",
            left_key="legal_name",
            right_type="w9",
            right_key="legal_name",
            label="legal name",
            right_label="W-9",
            normalizer=normalize_name,
        ),
        _compare(
            documents,
            left_type="application_kyc",
            left_key="ein",
            right_type="w9",
            right_key="ein",
            label="EIN",
            right_label="W-9",
            normalizer=normalize_identifier,
        ),
        _compare(
            documents,
            left_type="application_kyc",
            left_key="legal_name",
            right_type="certificate_of_insurance",
            right_key="insured_name",
            label="legal name",
            right_label="certificate of insurance",
            normalizer=normalize_name,
        ),
        _compare(
            documents,
            left_type="application_kyc",
            left_key="legal_name",
            right_type="mc_authority",
            right_key="legal_name",
            label="legal name",
            right_label="MC authority snapshot",
            normalizer=normalize_name,
        ),
        _compare(
            documents,
            left_type="application_kyc",
            left_key="mc_number",
            right_type="mc_authority",
            right_key="mc_number",
            label="MC number",
            right_label="MC authority snapshot",
            normalizer=normalize_identifier,
        ),
        _compare(
            documents,
            left_type="application_kyc",
            left_key="dot_number",
            right_type="mc_authority",
            right_key="dot_number",
            label="DOT number",
            right_label="MC authority snapshot",
            normalizer=normalize_identifier,
        ),
    )
    mismatches = [check for check in checks if check is not None]
    if not mismatches:
        return []

    details = " ".join(detail for detail, _evidence in mismatches)
    return [
        make_flag(
            "identity_mismatch",
            rule_id="identity.cross_document",
            details=details,
            evidence=mismatches[0][1],
        )
    ]


def validate_insurance(documents: Documents, as_of: date) -> list[RuleFlag]:
    document = documents.get("certificate_of_insurance")
    if document is None:
        return []

    flags: list[RuleFlag] = []
    expires_value = documents.field("certificate_of_insurance", "expires_on")
    expires_on = parse_date(expires_value.value if expires_value else None)
    if expires_value is not None and expires_on is not None and expires_on < as_of:
        flags.append(
            make_flag(
                "insurance_expired",
                rule_id="insurance.expiry",
                details=(
                    f"Policy expired on {expires_on.isoformat()}; "
                    f"the as-of date is {as_of.isoformat()}."
                ),
                evidence=evidence_for(expires_value, "certificate_of_insurance"),
            )
        )

    auto_value = documents.field("certificate_of_insurance", "auto_liability_limit")
    cargo_value = documents.field("certificate_of_insurance", "cargo_limit")
    auto_limit = parse_money(auto_value.value if auto_value else None)
    cargo_limit = parse_money(cargo_value.value if cargo_value else None)
    if (auto_limit is not None and auto_limit < AUTO_LIABILITY_MINIMUM) or (
        cargo_limit is not None and cargo_limit < CARGO_MINIMUM
    ):
        auto_text = format_money(auto_limit) if auto_limit is not None else "not stated"
        cargo_text = format_money(cargo_limit) if cargo_limit is not None else "not stated"
        evidence_value = auto_value if auto_limit is not None else cargo_value
        assert evidence_value is not None
        flags.append(
            make_flag(
                "insurance_below_minimum",
                rule_id="insurance.minimum_limits",
                details=(
                    f"Auto liability {auto_text} and cargo {cargo_text} are below the "
                    "$1,000,000.00 auto and $100,000.00 cargo minimums."
                ),
                evidence=evidence_for(evidence_value, "certificate_of_insurance"),
            )
        )
    return flags


def validate_authority(documents: Documents, as_of: date) -> list[RuleFlag]:
    document = documents.get("mc_authority")
    if document is None:
        return []

    status_value = documents.field("mc_authority", "authority_status")
    status = status_value.value.strip().casefold() if status_value is not None else None
    if status is not None and status != "active":
        notes = documents.value("mc_authority", "authority_notes")
        suffix = f" ({notes})" if notes else ""
        return [
            make_flag(
                "authority_inactive",
                rule_id="authority.status",
                details=f"Authority status is {status_value.value!r}{suffix}.",
                evidence=evidence_for(status_value, "mc_authority"),
            )
        ]

    granted_value = documents.field("mc_authority", "authority_granted_on")
    granted_on = parse_date(granted_value.value if granted_value else None)
    if (
        status == "active"
        and granted_value is not None
        and granted_on is not None
        and granted_on > as_of - timedelta(days=183)
    ):
        return [
            make_flag(
                "authority_young",
                rule_id="authority.age",
                details=(
                    f"Authority was granted on {granted_on.isoformat()}, less than six months "
                    f"before {as_of.isoformat()}."
                ),
                evidence=evidence_for(granted_value, "mc_authority"),
            )
        ]
    return []


def validate_watchlist(packet: CasePacket) -> list[RuleFlag]:
    record = packet.record("watchlist")
    if not isinstance(record, WatchlistRecord) or not record.matches:
        return []

    match = record.matches[0]
    return [
        make_flag(
            "watchlist_hit",
            rule_id="watchlist.match",
            details=(
                f"Synthetic watchlist matched {match.matched_name!r} "
                f"(score {match.match_score:.2f}) for {match.name!r}."
            ),
            evidence=Evidence(record_id=record.record_id, record_path="matches[0]"),
        )
    ]


__all__ = [
    "validate_authority",
    "validate_identity",
    "validate_insurance",
    "validate_watchlist",
]
