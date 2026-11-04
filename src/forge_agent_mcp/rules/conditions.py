"""Specific remedial conditions for non-approval recommendations."""

from __future__ import annotations

from .catalog import DOCUMENT_TITLES, RULE_CONDITIONS, injection_location
from .models import RuleFlag


def _missing_document_condition(flag: RuleFlag) -> str:
    document_type = flag.evidence.missing_document_type
    if document_type == "proof_of_delivery":
        return "Obtain a signed proof of delivery."
    if document_type == "certificate_of_insurance":
        return "Obtain a current certificate of insurance."
    title = (
        DOCUMENT_TITLES.get(document_type, "missing document")
        if document_type is not None
        else "missing document"
    )
    return f"Obtain a current {title[0].lower()}{title[1:]}."


def condition_for(flag: RuleFlag) -> str | None:
    condition = RULE_CONDITIONS.get(flag.rule_id)
    if condition is not None:
        return condition
    if flag.rule_id == "documents.required":
        return _missing_document_condition(flag)
    if flag.rule_id == "injection.document_instruction":
        location = injection_location(flag.evidence.document_type)
        return f"Quarantine the injected {location} text and review the packet manually."
    return None


__all__ = ["condition_for"]
