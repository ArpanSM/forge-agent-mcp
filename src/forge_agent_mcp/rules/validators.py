"""Deterministic validators over extracted fields and structured records."""

from __future__ import annotations

from collections.abc import Callable

from ..extraction.models import ExtractedValue
from .catalog import make_flag
from .models import Evidence, RuleFlag
from .values import Documents, evidence_for, normalize_identifier, normalize_name


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


__all__ = ["validate_identity"]
