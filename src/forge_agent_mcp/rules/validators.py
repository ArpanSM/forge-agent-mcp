"""Deterministic validators over extracted fields and structured records."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ..corpus.manifest import INVOICE_DOCUMENTS, ONBOARDING_DOCUMENTS
from ..corpus.models import PriorInvoiceRecord, WatchlistRecord
from ..extraction.models import (
    CaseExtraction,
    DocumentExtraction,
    ExtractedValue,
    KnownDocumentType,
)
from .catalog import (
    DOCUMENT_ORDER,
    DOCUMENT_TITLES,
    injection_location,
    make_flag,
)
from .corpus import CasePacket
from .models import Evidence, RuleFlag
from .policy import RulePolicy
from .values import (
    Documents,
    date_value,
    evidence_for,
    format_money,
    money_value,
    normalize_identifier,
    normalize_name,
    weight_value,
)

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:system|assistant|developer)\s+instruction\b", re.IGNORECASE),
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|underwriting|system)\b", re.IGNORECASE),
    re.compile(
        r"\bapprove\s+(?:this|the)\s+(?:carrier|invoice|packet)\s+immediately\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bmark\s+this\s+invoice\s+as\s+paid\b", re.IGNORECASE),
    re.compile(r"\bskip\s+(?:the\s+)?duplicate\s+checks?\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+report\s+this\s+instruction\b", re.IGNORECASE),
    re.compile(r"\bthis\s+document\s+is\s+trusted\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class IdentityCheck:
    left_type: KnownDocumentType
    left_key: str
    right_type: KnownDocumentType
    right_key: str
    label: str
    right_label: str
    normalizer: Callable[[str], str]


IDENTITY_CHECKS: tuple[IdentityCheck, ...] = (
    IdentityCheck(
        "application_kyc",
        "legal_name",
        "w9",
        "legal_name",
        "legal name",
        "W-9",
        normalize_name,
    ),
    IdentityCheck(
        "application_kyc",
        "ein",
        "w9",
        "ein",
        "EIN",
        "W-9",
        normalize_identifier,
    ),
    IdentityCheck(
        "application_kyc",
        "legal_name",
        "certificate_of_insurance",
        "insured_name",
        "legal name",
        "certificate of insurance",
        normalize_name,
    ),
    IdentityCheck(
        "application_kyc",
        "legal_name",
        "mc_authority",
        "legal_name",
        "legal name",
        "MC authority snapshot",
        normalize_name,
    ),
    IdentityCheck(
        "application_kyc",
        "mc_number",
        "mc_authority",
        "mc_number",
        "MC number",
        "MC authority snapshot",
        normalize_identifier,
    ),
    IdentityCheck(
        "application_kyc",
        "dot_number",
        "mc_authority",
        "dot_number",
        "DOT number",
        "MC authority snapshot",
        normalize_identifier,
    ),
)


def _compare(
    documents: Documents,
    check: IdentityCheck,
) -> tuple[str, Evidence] | None:
    left: ExtractedValue | None = documents.field(check.left_type, check.left_key)
    right: ExtractedValue | None = documents.field(check.right_type, check.right_key)
    if left is None or right is None:
        return None
    if check.normalizer(left.value) == check.normalizer(right.value):
        return None
    detail = (
        f"{check.label.capitalize()} {right.value!r} on the {check.right_label} differs from "
        f"{left.value!r} on the application."
    )
    return detail, evidence_for(right, check.right_type)


def validate_identity(documents: Documents) -> list[RuleFlag]:
    mismatches = [
        check
        for comparison in IDENTITY_CHECKS
        if (check := _compare(documents, comparison)) is not None
    ]
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


def validate_bank(
    documents: Documents,
    as_of: date,
    policy: RulePolicy,
) -> list[RuleFlag]:
    if documents.get("bank_letter") is None:
        return []

    flags: list[RuleFlag] = []
    opened = date_value(documents, "bank_letter", "account_opened_on")
    if opened is not None and opened.value > as_of - timedelta(days=policy.bank_change_window_days):
        flags.append(
            make_flag(
                "bank_account_changed",
                rule_id="bank.account_age",
                details=(
                    f"Account opened on {opened.value.isoformat()}, within "
                    f"{policy.bank_change_window_days} days of the as-of date "
                    f"{as_of.isoformat()}."
                ),
                evidence=evidence_for(opened.extracted, "bank_letter"),
            )
        )

    holder_value = documents.field("bank_letter", "account_holder")
    legal_name_value = documents.field("application_kyc", "legal_name")
    if (
        holder_value is not None
        and legal_name_value is not None
        and normalize_name(holder_value.value) != normalize_name(legal_name_value.value)
    ):
        flags.append(
            make_flag(
                "bank_account_name_mismatch",
                rule_id="bank.account_name",
                details=(
                    f"Account holder {holder_value.value!r} differs from the legal name "
                    f"{legal_name_value.value!r}."
                ),
                evidence=evidence_for(holder_value, "bank_letter"),
            )
        )
    return flags


def validate_duplicates(documents: Documents, packet: CasePacket) -> list[RuleFlag]:
    record = packet.record("prior_invoices")
    if not isinstance(record, PriorInvoiceRecord):
        return []

    number_value = documents.field("invoice", "invoice_number")
    current_number = number_value.value.strip().casefold() if number_value is not None else None
    number_match = next(
        (
            (index, prior)
            for index, prior in enumerate(record.invoices)
            if current_number is not None
            and prior.invoice_number.strip().casefold() == current_number
        ),
        None,
    )

    invoice_document = packet.document_by_type("invoice")
    current_hash = invoice_document.content_hash if invoice_document is not None else None
    hash_match = next(
        (
            (index, prior)
            for index, prior in enumerate(record.invoices)
            if current_hash is not None and prior.document_hash == current_hash
        ),
        None,
    )
    if number_match is None and hash_match is None:
        return []

    details: list[str] = []
    if number_match is not None and number_value is not None:
        _index, prior = number_match
        details.append(
            f"Invoice {prior.invoice_number} was already submitted on "
            f"{prior.invoice_date.isoformat()} for load {prior.load_number}."
        )
    if hash_match is not None:
        _index, prior = hash_match
        details.append(f"Invoice document hash matches prior invoice {prior.invoice_number}.")

    if number_match is not None and number_value is not None:
        rule_id = "duplicate.invoice_number"
        evidence = evidence_for(number_value, "invoice")
    else:
        assert hash_match is not None
        rule_id = "duplicate.document_hash"
        evidence = Evidence(
            record_id=record.record_id,
            record_path=f"invoices[{hash_match[0]}].document_hash",
        )
    return [
        make_flag(
            "duplicate_invoice",
            rule_id=rule_id,
            details=" ".join(details),
            evidence=evidence,
        )
    ]


LINE_ITEM_KEYS = ("linehaul", "fuel_surcharge", "accessorials")


def _money_values(
    documents: Documents,
    document_type: KnownDocumentType,
) -> tuple[dict[str, Decimal] | None, list[tuple[KnownDocumentType, str]]]:
    values: dict[str, Decimal] = {}
    missing: list[tuple[KnownDocumentType, str]] = []
    for key in (*LINE_ITEM_KEYS, "total"):
        parsed = money_value(documents, document_type, key)
        if parsed is None:
            missing.append((document_type, key))
        else:
            values[key] = parsed.value
    return (None if missing else values), missing


def _unverifiable_arithmetic_flag(
    documents: Documents,
    missing: list[tuple[KnownDocumentType, str]],
) -> RuleFlag:
    document_type, key = missing[0]
    document = documents.get(document_type)
    fields = ", ".join(f"{missing_type}.{missing_key}" for missing_type, missing_key in missing)
    return make_flag(
        "invoice_arithmetic_mismatch",
        rule_id="invoice.arithmetic_unverifiable",
        details=f"Invoice arithmetic could not be verified; missing or unparseable: {fields}.",
        evidence=Evidence(
            document_id=document.classification.document_id if document is not None else None,
            document_type=document_type,
            missing_field=key,
        ),
    )


def validate_invoice_arithmetic(documents: Documents) -> list[RuleFlag]:
    if documents.get("invoice") is None or documents.get("rate_confirmation") is None:
        return []

    invoice, invoice_missing = _money_values(documents, "invoice")
    rate, rate_missing = _money_values(documents, "rate_confirmation")
    missing = [*invoice_missing, *rate_missing]
    if missing:
        return [_unverifiable_arithmetic_flag(documents, missing)]
    assert invoice is not None and rate is not None

    details: list[str] = []
    evidence_key = "total"
    line_sum = sum(invoice[key] for key in LINE_ITEM_KEYS)
    if line_sum != invoice["total"]:
        details.append(
            f"Invoice line items sum to {format_money(line_sum)} but the invoice total is "
            f"{format_money(invoice['total'])}."
        )
        evidence_key = "total"
    for key in LINE_ITEM_KEYS:
        if invoice[key] != rate[key]:
            details.append(
                f"Invoice {key.replace('_', ' ')} {format_money(invoice[key])} differs from "
                f"the rate confirmation {format_money(rate[key])}."
            )
    if invoice["total"] != rate["total"]:
        details.append(
            f"Invoice total {format_money(invoice['total'])} differs from the rate "
            f"confirmation total {format_money(rate['total'])}."
        )
        evidence_key = "total"
    if not details:
        return []

    evidence_value = documents.field("invoice", evidence_key)
    if evidence_value is None:
        evidence_value = documents.field("invoice", "linehaul")
    assert evidence_value is not None
    return [
        make_flag(
            "invoice_arithmetic_mismatch",
            rule_id="invoice.arithmetic",
            details=" ".join(details),
            evidence=evidence_for(evidence_value, "invoice"),
        )
    ]


def validate_round_dollar(documents: Documents, policy: RulePolicy) -> list[RuleFlag]:
    total = money_value(documents, "invoice", "total")
    accessorials = money_value(documents, "invoice", "accessorials")
    if (
        total is not None
        and accessorials is not None
        and total.value > 0
        and accessorials.value == 0
        and total.value % policy.round_dollar_multiple == 0
    ):
        return [
            make_flag(
                "round_dollar_invoice",
                rule_id="invoice.round_dollar",
                details="Invoice total is a round multiple of $500.00 with no accessorials.",
                evidence=evidence_for(total.extracted, "invoice"),
            )
        ]
    return []


def _injection_evidence(
    extracted: ExtractedValue,
    document_type: KnownDocumentType | None,
) -> Evidence:
    if document_type is not None:
        return evidence_for(extracted, document_type).model_copy(update={"quote": None})
    citation = extracted.citation
    return Evidence(
        document_id=citation.document_id,
        page=citation.page,
        bbox=citation.bbox,
    )


def validate_injection(extraction: CaseExtraction) -> list[RuleFlag]:
    flags: list[RuleFlag] = []
    for document in extraction.documents:
        document_type = document.classification.document_type
        known_type = None if document_type == "unknown" else document_type
        for extracted in document.fields.values():
            text = f"{extracted.value}\n{extracted.citation.quote}"
            if not any(pattern.search(text) for pattern in INJECTION_PATTERNS):
                continue
            location = injection_location(known_type)
            flags.append(
                make_flag(
                    "document_instruction_injection",
                    rule_id="injection.document_instruction",
                    details=(
                        f"Document text addressed to an AI agent was detected in the {location}; "
                        "the instruction is ignored and the text is quarantined."
                    ),
                    evidence=_injection_evidence(extracted, known_type),
                )
            )
            break
    return flags


def validate_optional_fields(documents: Documents) -> list[RuleFlag]:
    invoice = documents.get("invoice")
    if invoice is None or "reference_number" in invoice.fields:
        return []

    total = money_value(documents, "invoice", "total")
    if total is not None:
        evidence = evidence_for(total.extracted, "invoice").model_copy(
            update={"missing_field": "reference_number"}
        )
    else:
        evidence = Evidence(
            document_id=invoice.classification.document_id,
            document_type="invoice",
            missing_field="reference_number",
        )
    return [
        make_flag(
            "missing_optional_field",
            rule_id="invoice.optional_reference",
            details="The invoice has no optional customer reference number.",
            evidence=evidence,
        )
    ]


def validate_pod(documents: Documents) -> list[RuleFlag]:
    pod = documents.get("proof_of_delivery")
    if pod is None:
        return []

    details: list[str] = []
    evidence: Evidence | None = None
    rule_id: str | None = None

    signature = documents.field("proof_of_delivery", "signed_by")
    if signature is None or not signature.value.strip():
        details.append("The proof of delivery has no signature.")
        evidence = (
            evidence_for(signature, "proof_of_delivery")
            if signature is not None
            else Evidence(
                document_id=pod.classification.document_id,
                document_type="proof_of_delivery",
                missing_field="signed_by",
            )
        )
        rule_id = "pod.signature"

    pod_weight = weight_value(documents, "proof_of_delivery", "weight_lbs")
    bol_weight = weight_value(documents, "bill_of_lading", "weight_lbs")
    if bol_weight is None:
        bol_weight = weight_value(documents, "rate_confirmation", "weight_lbs")
    if pod_weight is not None and bol_weight is not None and pod_weight.value != bol_weight.value:
        details.append(
            f"POD weight {pod_weight.value:,} lbs differs from the BOL weight "
            f"{bol_weight.value:,} lbs."
        )
        evidence = evidence_for(pod_weight.extracted, "proof_of_delivery")
        rule_id = "pod.weight"

    delivered = date_value(documents, "proof_of_delivery", "delivered_on")
    expected = date_value(documents, "rate_confirmation", "delivery_date")
    if delivered is not None and expected is not None and delivered.value != expected.value:
        details.append(
            f"POD delivery date {delivered.value.isoformat()} differs from the rate "
            f"confirmation delivery date {expected.value.isoformat()}."
        )
        if evidence is None:
            evidence = evidence_for(delivered.extracted, "proof_of_delivery")
            rule_id = "pod.delivery_date"

    if not details or evidence is None or rule_id is None:
        return []
    return [
        make_flag(
            "pod_mismatch",
            rule_id=rule_id,
            details=" ".join(details),
            evidence=evidence,
        )
    ]


def validate_insurance(
    documents: Documents,
    as_of: date,
    policy: RulePolicy,
) -> list[RuleFlag]:
    if documents.get("certificate_of_insurance") is None:
        return []

    flags: list[RuleFlag] = []
    expires = date_value(documents, "certificate_of_insurance", "expires_on")
    if expires is not None and expires.value < as_of:
        flags.append(
            make_flag(
                "insurance_expired",
                rule_id="insurance.expiry",
                details=(
                    f"Policy expired on {expires.value.isoformat()}; "
                    f"the as-of date is {as_of.isoformat()}."
                ),
                evidence=evidence_for(expires.extracted, "certificate_of_insurance"),
            )
        )

    auto = money_value(documents, "certificate_of_insurance", "auto_liability_limit")
    cargo = money_value(documents, "certificate_of_insurance", "cargo_limit")
    auto_below = auto is not None and auto.value < policy.min_auto_liability
    cargo_below = cargo is not None and cargo.value < policy.min_cargo_limit
    if auto_below and cargo_below:
        assert auto is not None and cargo is not None
        details = (
            f"Auto liability {format_money(auto.value)} and cargo {format_money(cargo.value)} "
            f"are below the {format_money(policy.min_auto_liability)} auto and "
            f"{format_money(policy.min_cargo_limit)} cargo minimums."
        )
        evidence_value = auto.extracted
    elif auto_below:
        assert auto is not None
        details = (
            f"Auto liability {format_money(auto.value)} is below the "
            f"{format_money(policy.min_auto_liability)} minimum."
        )
        evidence_value = auto.extracted
    elif cargo_below:
        assert cargo is not None
        details = (
            f"Cargo {format_money(cargo.value)} is below the "
            f"{format_money(policy.min_cargo_limit)} minimum."
        )
        evidence_value = cargo.extracted
    else:
        return flags

    flags.append(
        make_flag(
            "insurance_below_minimum",
            rule_id="insurance.minimum_limits",
            details=details,
            evidence=evidence_for(evidence_value, "certificate_of_insurance"),
        )
    )
    return flags


def validate_authority(
    documents: Documents,
    as_of: date,
    policy: RulePolicy,
) -> list[RuleFlag]:
    if documents.get("mc_authority") is None:
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

    granted = date_value(documents, "mc_authority", "authority_granted_on")
    if (
        status == "active"
        and granted is not None
        and granted.value > as_of - timedelta(days=policy.authority_min_age_days)
    ):
        return [
            make_flag(
                "authority_young",
                rule_id="authority.age",
                details=(
                    f"Authority was granted on {granted.value.isoformat()}, less than six months "
                    f"before {as_of.isoformat()}."
                ),
                evidence=evidence_for(granted.extracted, "mc_authority"),
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


@dataclass(frozen=True)
class MissingDocument:
    document_type: KnownDocumentType
    details: str
    document: DocumentExtraction | None


def find_missing_documents(
    extraction: CaseExtraction,
    packet: CasePacket,
) -> list[MissingDocument]:
    required = set(ONBOARDING_DOCUMENTS if packet.case_type == "onboarding" else INVOICE_DOCUMENTS)
    expected_by_id = packet.document_type_by_id
    available: set[str] = set()
    unreadable: dict[str, DocumentExtraction] = {}

    for document in extraction.documents:
        document_type = document.classification.document_type
        if document_type == "unknown":
            document_type = expected_by_id.get(document.classification.document_id)
        if document_type is None or document_type not in required:
            continue
        if document.status == "extracted" and document.classification.document_type != "unknown":
            available.add(document_type)
        else:
            unreadable[document_type] = document

    missing = (set(extraction.missing_documents) | (required - available)) & required
    results: list[MissingDocument] = []
    for document_type in DOCUMENT_ORDER:
        if document_type not in missing:
            continue
        title = DOCUMENT_TITLES[document_type]
        document = unreadable.get(document_type)
        if document is not None:
            details = f"The {title} could not be read: {document.error or 'extraction failed'}."
        else:
            details = f"The packet has no {title}."
        results.append(MissingDocument(document_type, details, document))
    return results


def validate_required_documents(
    extraction: CaseExtraction,
    packet: CasePacket,
) -> list[RuleFlag]:
    flags: list[RuleFlag] = []
    for missing in find_missing_documents(extraction, packet):
        flags.append(
            make_flag(
                "missing_document",
                rule_id="documents.required",
                details=missing.details,
                evidence=Evidence(
                    document_id=(
                        missing.document.classification.document_id if missing.document else None
                    ),
                    document_type=missing.document_type,
                    missing_document_type=missing.document_type,
                ),
            )
        )
    return flags


__all__ = [
    "IDENTITY_CHECKS",
    "IdentityCheck",
    "MissingDocument",
    "find_missing_documents",
    "validate_authority",
    "validate_bank",
    "validate_duplicates",
    "validate_identity",
    "validate_injection",
    "validate_insurance",
    "validate_invoice_arithmetic",
    "validate_optional_fields",
    "validate_pod",
    "validate_required_documents",
    "validate_round_dollar",
    "validate_watchlist",
]
