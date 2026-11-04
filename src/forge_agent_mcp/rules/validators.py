"""Deterministic validators over extracted fields and structured records."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ..corpus.manifest import INVOICE_DOCUMENTS, ONBOARDING_DOCUMENTS
from ..corpus.models import PriorInvoiceRecord, WatchlistRecord
from ..extraction.models import CaseExtraction, DocumentExtraction, ExtractedValue
from .catalog import DOCUMENT_ORDER, DOCUMENT_TITLES, make_flag
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
    parse_weight,
)

AUTO_LIABILITY_MINIMUM = Decimal("1000000.00")
CARGO_MINIMUM = Decimal("100000.00")

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


def validate_bank(documents: Documents, as_of: date) -> list[RuleFlag]:
    if documents.get("bank_letter") is None:
        return []

    flags: list[RuleFlag] = []
    opened_value = documents.field("bank_letter", "account_opened_on")
    opened_on = parse_date(opened_value.value if opened_value else None)
    if (
        opened_value is not None
        and opened_on is not None
        and opened_on > as_of - timedelta(days=30)
    ):
        flags.append(
            make_flag(
                "bank_account_changed",
                rule_id="bank.account_age",
                details=(
                    f"Account opened on {opened_on.isoformat()}, within 30 days of the "
                    f"as-of date {as_of.isoformat()}."
                ),
                evidence=evidence_for(opened_value, "bank_letter"),
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
            if current_number is not None and prior.invoice_number.strip().casefold() == current_number
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


def _money_values(documents: Documents, document_type: str) -> dict[str, Decimal] | None:
    values: dict[str, Decimal] = {}
    for key in (*LINE_ITEM_KEYS, "total"):
        extracted = documents.field(document_type, key)
        parsed = parse_money(extracted.value if extracted else None)
        if parsed is None:
            return None
        values[key] = parsed
    return values


def validate_invoice_arithmetic(documents: Documents) -> list[RuleFlag]:
    invoice = _money_values(documents, "invoice")
    rate = _money_values(documents, "rate_confirmation")
    if invoice is None or rate is None:
        return []

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


def validate_round_dollar(documents: Documents) -> list[RuleFlag]:
    total_value = documents.field("invoice", "total")
    accessorials_value = documents.field("invoice", "accessorials")
    total = parse_money(total_value.value if total_value else None)
    accessorials = parse_money(accessorials_value.value if accessorials_value else None)
    if (
        total_value is not None
        and total is not None
        and total > 0
        and accessorials is not None
        and accessorials == 0
        and total % 500 == 0
    ):
        return [
            make_flag(
                "round_dollar_invoice",
                rule_id="invoice.round_dollar",
                details="Invoice total is a round multiple of $500.00 with no accessorials.",
                evidence=evidence_for(total_value, "invoice"),
            )
        ]
    return []


def _injection_location(document_type: str) -> str:
    if document_type == "application_kyc":
        return "application"
    if document_type == "proof_of_delivery":
        return "POD"
    return DOCUMENT_TITLES.get(document_type, "document").lower()


def validate_injection(extraction: CaseExtraction) -> list[RuleFlag]:
    flags: list[RuleFlag] = []
    for document in extraction.documents:
        document_type = document.classification.document_type
        for extracted in document.fields.values():
            text = f"{extracted.value}\n{extracted.citation.quote}"
            if not any(pattern.search(text) for pattern in INJECTION_PATTERNS):
                continue
            location = _injection_location(document_type)
            flags.append(
                make_flag(
                    "document_instruction_injection",
                    rule_id="injection.document_instruction",
                    details=(
                        f"Document text addressed to an AI agent was detected in the {location}; "
                        "the instruction is ignored and the text is quarantined."
                    ),
                    evidence=evidence_for(extracted, document_type),
                )
            )
            break
    return flags


def validate_optional_fields(documents: Documents) -> list[RuleFlag]:
    invoice = documents.get("invoice")
    if invoice is None or "reference_number" in invoice.fields:
        return []

    total_value = documents.field("invoice", "total")
    if total_value is not None:
        evidence = evidence_for(total_value, "invoice").model_copy(
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
    rule_id = "pod.contents"

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

    pod_weight_value = documents.field("proof_of_delivery", "weight_lbs")
    pod_weight = parse_weight(pod_weight_value.value if pod_weight_value else None)
    bol_weight_value = documents.field("bill_of_lading", "weight_lbs")
    bol_weight = parse_weight(bol_weight_value.value if bol_weight_value else None)
    if bol_weight is None:
        rate_weight_value = documents.field("rate_confirmation", "weight_lbs")
        bol_weight = parse_weight(rate_weight_value.value if rate_weight_value else None)
    if (
        pod_weight_value is not None
        and pod_weight is not None
        and bol_weight is not None
        and pod_weight != bol_weight
    ):
        details.append(
            f"POD weight {pod_weight:,} lbs differs from the BOL weight {bol_weight:,} lbs."
        )
        evidence = evidence_for(pod_weight_value, "proof_of_delivery")
        rule_id = "pod.weight"

    delivered_value = documents.field("proof_of_delivery", "delivered_on")
    delivered_on = parse_date(delivered_value.value if delivered_value else None)
    expected_value = documents.field("rate_confirmation", "delivery_date")
    expected_on = parse_date(expected_value.value if expected_value else None)
    if (
        delivered_value is not None
        and delivered_on is not None
        and expected_on is not None
        and delivered_on != expected_on
    ):
        details.append(
            f"POD delivery date {delivered_on.isoformat()} differs from the rate confirmation "
            f"delivery date {expected_on.isoformat()}."
        )
        if evidence is None:
            evidence = evidence_for(delivered_value, "proof_of_delivery")
            rule_id = "pod.delivery_date"

    if not details or evidence is None:
        return []
    return [
        make_flag(
            "pod_mismatch",
            rule_id=rule_id,
            details=" ".join(details),
            evidence=evidence,
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


@dataclass(frozen=True)
class MissingDocument:
    document_type: str
    details: str
    document: DocumentExtraction | None


def find_missing_documents(
    extraction: CaseExtraction,
    packet: CasePacket,
) -> list[MissingDocument]:
    required = ONBOARDING_DOCUMENTS if packet.case_type == "onboarding" else INVOICE_DOCUMENTS
    expected_by_id = packet.document_type_by_id
    available: set[str] = set()
    unreadable: dict[str, DocumentExtraction] = {}

    for document in extraction.documents:
        document_type = document.classification.document_type
        if document_type == "unknown":
            document_type = expected_by_id.get(document.classification.document_id)
        if document_type not in required:
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
    "MissingDocument",
    "find_missing_documents",
    "validate_authority",
    "validate_bank",
    "validate_duplicates",
    "validate_identity",
    "validate_insurance",
    "validate_injection",
    "validate_invoice_arithmetic",
    "validate_optional_fields",
    "validate_pod",
    "validate_round_dollar",
    "validate_required_documents",
    "validate_watchlist",
]
