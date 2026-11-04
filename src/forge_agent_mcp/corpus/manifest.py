"""Typed corpus manifest and the validator that guards its distribution."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

CaseType = Literal["onboarding", "invoice"]
Split = Literal["eval", "held_out"]
Category = Literal["clean", "hard_stop", "high", "medium", "low", "injection"]
Severity = Literal["hard_stop", "high", "medium", "low"]
Recommendation = Literal[
    "approve",
    "conditional_approve",
    "hold",
    "request_documents",
    "decline",
    "escalate",
]
Decision = Literal["approve", "conditional_approve", "decline", "request_documents"]
DecisionEffect = Literal[
    "decline_or_escalate",
    "hold_or_condition",
    "condition_or_reserve",
    "approve_with_clarification",
]

ONBOARDING_DOCUMENTS = {
    "application_kyc",
    "w9",
    "certificate_of_insurance",
    "bank_letter",
    "mc_authority",
}
INVOICE_DOCUMENTS = {
    "rate_confirmation",
    "bill_of_lading",
    "proof_of_delivery",
    "invoice",
}
REQUIRED_RECORDS = {
    "onboarding": {"ucc_filing", "watchlist"},
    "invoice": {"prior_invoices", "broker_bond"},
}

HARD_STOP_ARCHETYPE_FLAGS: dict[str, set[str]] = {
    "expired_insurance": {"insurance_expired", "insurance_below_minimum"},
    "identity_mismatch": {"identity_mismatch"},
    "watchlist_hit": {"watchlist_hit"},
    "inactive_authority": {"authority_inactive"},
    "duplicate_invoice": {"duplicate_invoice"},
}


class ManifestError(ValueError):
    """The manifest is missing, malformed or inconsistent."""

    def __init__(self, errors: str | list[str]) -> None:
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("manifest invalid: " + "; ".join(self.errors))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Factor(StrictModel):
    name: str
    address: str
    email: str
    phone: str


class Address(StrictModel):
    street: str
    city: str
    state: str
    postal_code: str

    def one_line(self) -> str:
        return f"{self.street}, {self.city}, {self.state} {self.postal_code}"


class Authority(StrictModel):
    status: Literal["active", "inactive"]
    granted_on: date
    updated_on: date
    revoked_on: date | None = None
    reason: str | None = None
    operating_radius: str
    safety_rating: str


class Insurance(StrictModel):
    insurer: str
    policy_number: str
    auto_liability_limit: int = Field(gt=0)
    cargo_limit: int = Field(gt=0)
    general_liability_limit: int = Field(gt=0)
    effective_on: date
    expires_on: date
    certificate_holder: str

    @property
    def below_minimum(self) -> bool:
        return self.auto_liability_limit < 1_000_000 or self.cargo_limit < 100_000


class BankAccount(StrictModel):
    bank_name: str
    routing_number: str
    account_number: str
    account_name: str
    opened_on: date


class UccRecord(StrictModel):
    status: Literal["no_filing", "active"]
    filed_on: date | None = None
    secured_party: str
    collateral: str


class WatchlistMatch(StrictModel):
    name: str
    matched_name: str
    match_score: float = Field(ge=0.0, le=1.0)
    reason: str
    source: str


class Watchlist(StrictModel):
    checked_on: date
    list_name: str
    matches: list[WatchlistMatch] = Field(default_factory=list)


class BrokerBond(StrictModel):
    broker_name: str
    broker_mc: str
    bond_active: bool
    bond_amount: int = Field(gt=0)
    effective_on: date
    expires_on: date
    surety: str


class Load(StrictModel):
    load_number: str
    origin: str
    destination: str
    pickup_date: date
    delivery_date: date
    weight_lbs: int = Field(gt=0)
    commodity: str
    shipper: str
    consignee: str


class RateConfirmation(StrictModel):
    rate_confirmation_number: str
    linehaul: int = Field(ge=0)
    fuel_surcharge: int = Field(ge=0)
    accessorials: int = Field(ge=0)
    total: int = Field(ge=0)
    payment_terms: str


class InvoiceData(StrictModel):
    invoice_number: str
    invoice_date: date
    due_date: date
    linehaul: int = Field(ge=0)
    fuel_surcharge: int = Field(ge=0)
    accessorials: int = Field(ge=0)
    total: int = Field(ge=0)


class Pod(StrictModel):
    pod_number: str
    delivered_on: date
    weight_lbs: int = Field(gt=0)
    signed_by: str
    notes: str = ""


class PriorInvoice(StrictModel):
    invoice_number: str
    load_number: str
    invoice_date: date
    amount: int = Field(ge=0)
    document_hash: str | None = None


class Carrier(StrictModel):
    carrier_id: str
    legal_name: str
    dba: str
    ein: str
    mc_number: str
    dot_number: str
    address: Address
    phone: str
    email: str
    owner_name: str
    owner_email: str
    formation_date: date
    authority: Authority
    insurance: Insurance
    bank: BankAccount
    ucc: UccRecord
    watchlist: Watchlist
    broker_bond: BrokerBond
    load: Load
    rate_confirmation: RateConfirmation
    invoice: InvoiceData
    pod: Pod | None
    prior_invoices: list[PriorInvoice] = Field(min_length=1)
    w9_legal_name: str | None = None
    w9_ein: str | None = None
    application_notes: str | None = None


class DocumentType(StrictModel):
    title: str
    code: str
    case_type: CaseType


class RecordType(StrictModel):
    title: str
    case_type: CaseType


class FlagDefinition(StrictModel):
    code: str
    severity: Severity
    rule: str
    decision_effect: DecisionEffect


class Case(StrictModel):
    case_id: str
    case_type: CaseType
    carrier_id: str
    split: Split
    category: Category
    archetypes: list[str] = Field(min_length=1)
    documents: list[str] = Field(min_length=1)
    structured_records: list[str] = Field(min_length=1)
    expected_flags: list[str]
    expected_recommendation: Recommendation
    expected_decision: Decision
    expected_conditions: list[str]
    notes: str = ""

    @property
    def has_injection(self) -> bool:
        return "prompt_injection" in self.archetypes


class CaseCounts(StrictModel):
    clean: int = Field(default=0, ge=0)
    hard_stop: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)
    injection: int = Field(default=0, ge=0)


class Distribution(StrictModel):
    onboarding: CaseCounts
    invoice: CaseCounts


class Manifest(StrictModel):
    schema_version: Literal[1]
    as_of: date
    watermark: Literal["SYNTHETIC"]
    factor: Factor
    required_hard_stop_archetypes: list[str] = Field(min_length=1)
    required_eval_archetypes: list[str] = Field(min_length=1)
    eval_distribution: Distribution
    held_out_distribution: Distribution
    document_types: dict[str, DocumentType]
    record_types: dict[str, RecordType]
    flag_catalog: list[FlagDefinition] = Field(min_length=1)
    carriers: list[Carrier] = Field(min_length=1)
    cases: list[Case] = Field(min_length=1)

    @property
    def flag_by_code(self) -> dict[str, FlagDefinition]:
        return {flag.code: flag for flag in self.flag_catalog}

    @property
    def carrier_by_id(self) -> dict[str, Carrier]:
        return {carrier.carrier_id: carrier for carrier in self.carriers}


def _format_validation_error(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    ]


def _unique(items: list[str], field: str, owner: str, errors: list[str]) -> None:
    duplicates = [item for item, count in Counter(items).items() if count > 1]
    if duplicates:
        errors.append(f"{owner}: duplicate {field} values {sorted(duplicates)}")


def _derive_category(case: Case, flags: dict[str, FlagDefinition]) -> Category:
    if case.has_injection:
        return "injection"
    severities = {flags[code].severity for code in case.expected_flags if code in flags}
    for severity in ("hard_stop", "high", "medium", "low"):
        if severity in severities:
            return severity  # type: ignore[return-value]
    return "clean"


def _derive_recommendation(case: Case, flags: dict[str, FlagDefinition]) -> Recommendation:
    definitions = [flags[code] for code in case.expected_flags if code in flags]
    if case.has_injection:
        return "hold"
    if any(flag.severity == "hard_stop" for flag in definitions):
        if "watchlist_hit" in case.expected_flags:
            return "escalate"
        return "decline"
    if "missing_document" in case.expected_flags:
        return "request_documents"
    high_count = sum(flag.severity == "high" for flag in definitions)
    if high_count >= 2:
        return "hold"
    if high_count == 1:
        return "conditional_approve"
    if any(flag.severity == "medium" for flag in definitions):
        return "conditional_approve"
    return "approve"


def _derive_decision(recommendation: Recommendation) -> Decision:
    if recommendation in {"decline", "escalate"}:
        return "decline"
    if recommendation in {"hold", "request_documents"}:
        return "request_documents"
    return recommendation


def _distribution_counts(cases: list[Case]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for case in cases:
        counts[case.case_type][case.category] += 1
    return {
        case_type: {
            category: counts[case_type][category]
            for category in ("clean", "hard_stop", "high", "medium", "low", "injection")
        }
        for case_type in ("onboarding", "invoice")
    }


def _expected_distribution(distribution: Distribution) -> dict[str, dict[str, int]]:
    return {
        "onboarding": distribution.onboarding.model_dump(),
        "invoice": distribution.invoice.model_dump(),
    }


def _validate_documents(case: Case, manifest: Manifest, errors: list[str]) -> None:
    required = ONBOARDING_DOCUMENTS if case.case_type == "onboarding" else INVOICE_DOCUMENTS
    actual = set(case.documents)
    missing = required - actual
    extra = actual - required

    allowed_missing: set[str] = set()
    if (
        case.case_type == "invoice"
        and "missing_document" in case.archetypes
        and "missing_document" in case.expected_flags
    ):
        allowed_missing = {"proof_of_delivery"}

    if missing - allowed_missing:
        errors.append(
            f"case {case.case_id}: missing required documents {sorted(missing - allowed_missing)}"
        )
    if extra:
        errors.append(f"case {case.case_id}: unknown documents {sorted(extra)}")
    if missing and not missing <= allowed_missing:
        errors.append(
            f"case {case.case_id}: proof_of_delivery is missing without a missing_document label"
        )

    for document_type in case.documents:
        definition = manifest.document_types.get(document_type)
        if definition is None:
            errors.append(f"case {case.case_id}: unknown document type {document_type!r}")
        elif definition.case_type != case.case_type:
            errors.append(
                f"case {case.case_id}: document {document_type!r} belongs to {definition.case_type}"
            )

    expected_records = REQUIRED_RECORDS[case.case_type]
    actual_records = set(case.structured_records)
    if actual_records != expected_records:
        errors.append(
            f"case {case.case_id}: structured records must be {sorted(expected_records)}, "
            f"found {sorted(actual_records)}"
        )
    for record_type in case.structured_records:
        definition = manifest.record_types.get(record_type)
        if definition is None:
            errors.append(f"case {case.case_id}: unknown record type {record_type!r}")
        elif definition.case_type != case.case_type:
            errors.append(
                f"case {case.case_id}: record {record_type!r} belongs to {definition.case_type}"
            )


def validate_manifest(raw: dict[str, Any]) -> Manifest:
    """Validate structure, references, labels and the fixed corpus distribution."""

    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(_format_validation_error(exc)) from exc

    errors: list[str] = []

    if len(manifest.carriers) != 8:
        errors.append(f"expected 8 carriers, found {len(manifest.carriers)}")
    if len(manifest.cases) != 16:
        errors.append(f"expected 16 cases, found {len(manifest.cases)}")

    case_types = Counter(case.case_type for case in manifest.cases)
    if case_types != {"onboarding": 8, "invoice": 8}:
        errors.append(f"expected 8 onboarding and 8 invoice cases, found {dict(case_types)}")
    split_counts = Counter(case.split for case in manifest.cases)
    if split_counts != {"eval": 12, "held_out": 4}:
        errors.append(f"expected 12 eval and 4 held-out cases, found {dict(split_counts)}")

    carrier_ids = {carrier.carrier_id for carrier in manifest.carriers}
    if len(carrier_ids) != len(manifest.carriers):
        errors.append("carrier_id values must be unique")
    case_ids = {case.case_id for case in manifest.cases}
    if len(case_ids) != len(manifest.cases):
        errors.append("case_id values must be unique")
    flag_codes = {flag.code for flag in manifest.flag_catalog}
    if len(flag_codes) != len(manifest.flag_catalog):
        errors.append("flag catalog codes must be unique")

    per_carrier_type: Counter[tuple[str, str]] = Counter(
        (case.carrier_id, case.case_type) for case in manifest.cases
    )
    for carrier in manifest.carriers:
        for case_type in ("onboarding", "invoice"):
            count = per_carrier_type[(carrier.carrier_id, case_type)]
            if count != 1:
                errors.append(
                    f"carrier {carrier.carrier_id}: expected one {case_type} case, found {count}"
                )

    flags_by_code = manifest.flag_by_code
    eval_cases = [case for case in manifest.cases if case.split == "eval"]
    held_out_cases = [case for case in manifest.cases if case.split == "held_out"]

    for case in manifest.cases:
        _unique(case.archetypes, "archetype", f"case {case.case_id}", errors)
        _unique(case.documents, "document", f"case {case.case_id}", errors)
        _unique(case.expected_flags, "flag", f"case {case.case_id}", errors)
        _unique(case.expected_conditions, "condition", f"case {case.case_id}", errors)

        if case.carrier_id not in carrier_ids:
            errors.append(f"case {case.case_id}: unknown carrier {case.carrier_id!r}")
        for code in case.expected_flags:
            if code not in flag_codes:
                errors.append(f"case {case.case_id}: unknown flag code {code!r}")

        _validate_documents(case, manifest, errors)

        if not set(case.expected_flags) <= flag_codes:
            continue

        derived_category = _derive_category(case, flags_by_code)
        if case.category != derived_category:
            errors.append(
                f"case {case.case_id}: category {case.category!r} does not match "
                f"flags (derived {derived_category!r})"
            )

        derived_recommendation = _derive_recommendation(case, flags_by_code)
        if case.expected_recommendation != derived_recommendation:
            errors.append(
                f"case {case.case_id}: expected recommendation "
                f"{case.expected_recommendation!r} contradicts flags "
                f"(derived {derived_recommendation!r})"
            )
        derived_decision = _derive_decision(derived_recommendation)
        if case.expected_decision != derived_decision:
            errors.append(
                f"case {case.case_id}: expected decision {case.expected_decision!r} "
                f"does not match recommendation {derived_recommendation!r}"
            )
        if (
            derived_recommendation in {"hold", "request_documents", "conditional_approve"}
            and not case.expected_conditions
        ):
            errors.append(f"case {case.case_id}: {derived_recommendation} requires a condition")

    for case in held_out_cases:
        if case.category == "hard_stop" and len(case.expected_flags) < 2:
            errors.append(
                f"case {case.case_id}: held-out hard-stop case must be multi-flag"
            )

    actual_eval = _distribution_counts(eval_cases)
    expected_eval = _expected_distribution(manifest.eval_distribution)
    if actual_eval != expected_eval:
        errors.append(
            f"eval distribution mismatch: expected {expected_eval}, found {actual_eval}"
        )

    actual_held_out = _distribution_counts(held_out_cases)
    expected_held_out = _expected_distribution(manifest.held_out_distribution)
    if actual_held_out != expected_held_out:
        errors.append(
            f"held-out distribution mismatch: expected {expected_held_out}, "
            f"found {actual_held_out}"
        )

    archetypes = {archetype for case in manifest.cases for archetype in case.archetypes}
    for archetype in manifest.required_hard_stop_archetypes:
        if archetype not in HARD_STOP_ARCHETYPE_FLAGS:
            errors.append(f"required hard-stop archetype {archetype!r} is unknown")
            continue
        matching = [case for case in manifest.cases if archetype in case.archetypes]
        expected_codes = HARD_STOP_ARCHETYPE_FLAGS[archetype]
        if not matching:
            errors.append(f"required hard-stop archetype {archetype!r} is not covered")
            continue
        if not any(set(case.expected_flags) & expected_codes for case in matching):
            errors.append(
                f"required hard-stop archetype {archetype!r} is not covered by a hard-stop flag"
            )

    for archetype in manifest.required_eval_archetypes:
        if archetype not in archetypes:
            errors.append(f"required eval archetype {archetype!r} is not covered")
        elif not any(archetype in case.archetypes for case in eval_cases):
            errors.append(f"required eval archetype {archetype!r} is not in the eval split")

    if errors:
        raise ManifestError(errors)
    return manifest


def load_manifest(path: str | Path) -> Manifest:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a JSON object")
    return validate_manifest(raw)
