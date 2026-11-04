"""The fixed flag taxonomy, severities, conditions and document titles.

The catalogue mirrors ``corpus/manifest.json`` except for the two flags listed in
``FUTURE_FLAG_CODES``: they are catalogued upstream but have no validator in this slice.
A test guards both the mirror and the future-work list. Flags carry the catalogue rule
text so every finding is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..corpus.vocab import DecisionEffect, Severity
from ..extraction.models import KnownDocumentType
from .models import Evidence, FlagCode, RuleFlag


@dataclass(frozen=True)
class FlagDefinition:
    code: FlagCode
    severity: Severity
    rule: str
    decision_effect: DecisionEffect


FLAG_CATALOG: tuple[FlagDefinition, ...] = (
    FlagDefinition(
        "watchlist_hit",
        "hard_stop",
        "watchlist.matches is not empty",
        "decline_or_escalate",
    ),
    FlagDefinition(
        "authority_inactive",
        "hard_stop",
        "authority.status is not active",
        "decline_or_escalate",
    ),
    FlagDefinition(
        "identity_mismatch",
        "hard_stop",
        "legal name, EIN or MC/DOT differs across application, W-9 and COI",
        "decline_or_escalate",
    ),
    FlagDefinition(
        "insurance_expired",
        "hard_stop",
        "insurance.expires_on is before as_of",
        "decline_or_escalate",
    ),
    FlagDefinition(
        "insurance_below_minimum",
        "hard_stop",
        "auto liability is below 1000000 or cargo is below 100000",
        "decline_or_escalate",
    ),
    FlagDefinition(
        "duplicate_invoice",
        "hard_stop",
        "invoice number or document hash appears in prior invoice history",
        "decline_or_escalate",
    ),
    FlagDefinition(
        "bank_account_changed",
        "high",
        "bank account opened within 30 days of as_of",
        "hold_or_condition",
    ),
    FlagDefinition(
        "bank_account_name_mismatch",
        "high",
        "bank account holder differs from the legal name",
        "hold_or_condition",
    ),
    FlagDefinition(
        "invoice_arithmetic_mismatch",
        "high",
        "invoice total differs from rate confirmation or line items do not sum",
        "hold_or_condition",
    ),
    FlagDefinition(
        "pod_mismatch",
        "high",
        "POD weight or delivery date differs from the BOL",
        "hold_or_condition",
    ),
    FlagDefinition(
        "missing_document",
        "high",
        "a required packet document is absent",
        "hold_or_condition",
    ),
    FlagDefinition(
        "document_instruction_injection",
        "high",
        "document text contains instructions aimed at an AI agent",
        "hold_or_condition",
    ),
    FlagDefinition(
        "authority_young",
        "medium",
        "authority granted within 6 months of as_of",
        "condition_or_reserve",
    ),
    FlagDefinition(
        "round_dollar_invoice",
        "medium",
        "invoice total is a whole multiple of 500 with no accessorials",
        "condition_or_reserve",
    ),
    FlagDefinition(
        "missing_optional_field",
        "low",
        "an optional contact or reference field is blank",
        "approve_with_clarification",
    ),
)

# Upstream catalogue entries deliberately deferred to a later slice.
FUTURE_FLAG_CODES: frozenset[str] = frozenset({"altered_metadata", "formatting_inconsistency"})

FLAG_BY_CODE: dict[FlagCode, FlagDefinition] = {flag.code: flag for flag in FLAG_CATALOG}
FLAG_ORDER: dict[FlagCode, int] = {flag.code: index for index, flag in enumerate(FLAG_CATALOG)}
SEVERITY_ORDER: dict[Severity, int] = {
    "hard_stop": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

DOCUMENT_ORDER: tuple[KnownDocumentType, ...] = (
    "application_kyc",
    "w9",
    "certificate_of_insurance",
    "bank_letter",
    "mc_authority",
    "rate_confirmation",
    "bill_of_lading",
    "proof_of_delivery",
    "invoice",
)

DOCUMENT_TITLES: dict[KnownDocumentType, str] = {
    "application_kyc": "Carrier Application / KYC",
    "w9": "IRS Form W-9 (Synthetic)",
    "certificate_of_insurance": "Certificate of Insurance",
    "bank_letter": "Bank Letter / Voided Check",
    "mc_authority": "MC Authority Snapshot",
    "rate_confirmation": "Rate Confirmation",
    "bill_of_lading": "Bill of Lading",
    "proof_of_delivery": "Proof of Delivery",
    "invoice": "Freight Invoice",
}

RULE_CONDITIONS: dict[str, str] = {
    "authority.age": "Obtain six months of authority history or adjust reserve.",
    "bank.account_age": "Verify the new bank account with a dated bank letter.",
    "bank.account_name": "Resolve the bank account-name mismatch.",
    "invoice.arithmetic": "Reconcile the invoice to the rate confirmation.",
    "invoice.arithmetic_unverifiable": (
        "Obtain a complete invoice and rate confirmation before funding."
    ),
    "invoice.round_dollar": "Adjust reserve for the round-dollar invoice.",
    "invoice.optional_reference": "Confirm the optional invoice reference before funding.",
    "pod.weight": "Obtain a POD matching the BOL weight.",
    "pod.delivery_date": "Obtain a POD matching the BOL delivery date.",
    "pod.signature": "Obtain a signed proof of delivery.",
}


def injection_location(document_type: KnownDocumentType | None) -> str:
    if document_type == "application_kyc":
        return "application"
    if document_type == "proof_of_delivery":
        return "POD"
    if document_type is None:
        return "document"
    return DOCUMENT_TITLES.get(document_type, "document").lower()


def make_flag(
    code: FlagCode,
    *,
    rule_id: str,
    details: str,
    evidence: Evidence,
) -> RuleFlag:
    definition = FLAG_BY_CODE[code]
    return RuleFlag(
        code=code,
        severity=definition.severity,
        rule_id=rule_id,
        rule=definition.rule,
        details=details,
        evidence=evidence,
    )


def sort_flags(flags: list[RuleFlag]) -> list[RuleFlag]:
    """Deterministic severity order, then catalogue order, then rule id."""

    return sorted(
        flags,
        key=lambda flag: (
            SEVERITY_ORDER[flag.severity],
            FLAG_ORDER.get(flag.code, len(FLAG_ORDER)),
            flag.rule_id,
        ),
    )


__all__ = [
    "DOCUMENT_ORDER",
    "DOCUMENT_TITLES",
    "FLAG_BY_CODE",
    "FLAG_CATALOG",
    "FLAG_ORDER",
    "FUTURE_FLAG_CODES",
    "FlagDefinition",
    "RULE_CONDITIONS",
    "SEVERITY_ORDER",
    "injection_location",
    "make_flag",
    "sort_flags",
]
