"""Generated corpus models: index, ground truth, citations and record payloads."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from .manifest import WatchlistMatch
from .vocab import (
    CaseType,
    Category,
    Decision,
    Recommendation,
    Severity,
    Split,
    StrictModel,
)


class Citation(StrictModel):
    document_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    record_path: str | None = None
    missing_document_type: str | None = None
    missing_field: str | None = None


class FieldTruth(StrictModel):
    field: str
    value: str
    citation: Citation


class FlagTruth(StrictModel):
    code: str
    severity: Severity
    rule: str
    evidence: Citation
    details: str


class DocumentEntry(StrictModel):
    document_id: str
    document_type: str
    title: str
    filename: str
    pages: int = Field(ge=1)
    content_hash: str
    sha256: str


class RecordEntry(StrictModel):
    record_id: str
    record_type: str
    title: str
    filename: str
    content_hash: str
    sha256: str


class BaseRecord(StrictModel):
    record_id: str
    record_type: str
    synthetic: Literal[True] = True
    carrier_id: str


class UccFilingRecord(BaseRecord):
    record_type: Literal["ucc_filing"] = "ucc_filing"
    status: Literal["no_filing", "active"]
    filed_on: date | None = None
    secured_party: str
    collateral: str


class WatchlistRecord(BaseRecord):
    record_type: Literal["watchlist"] = "watchlist"
    checked_on: date
    list_name: str
    matches: list[WatchlistMatch]


class PriorInvoiceLine(StrictModel):
    invoice_number: str
    load_number: str
    invoice_date: date
    amount: int = Field(ge=0)
    document_hash: str | None = None


class PriorInvoiceRecord(BaseRecord):
    record_type: Literal["prior_invoices"] = "prior_invoices"
    invoices: list[PriorInvoiceLine]


class BrokerBondRecord(BaseRecord):
    record_type: Literal["broker_bond"] = "broker_bond"
    broker_name: str
    broker_mc: str
    bond_active: bool
    bond_amount: int = Field(gt=0)
    effective_on: date
    expires_on: date
    surety: str


RecordPayload = Annotated[
    UccFilingRecord | WatchlistRecord | PriorInvoiceRecord | BrokerBondRecord,
    Field(discriminator="record_type"),
]


class CaseSummary(StrictModel):
    case_id: str
    case_type: CaseType
    carrier_id: str
    split: Split
    category: Category
    archetypes: list[str]
    documents: list[DocumentEntry]
    structured_records: list[RecordEntry]
    expected_recommendation: Recommendation
    expected_decision: Decision
    expected_conditions: list[str]


class CaseGroundTruth(CaseSummary):
    expected_fields: list[FieldTruth]
    expected_records: dict[str, RecordPayload]
    expected_flags: list[FlagTruth]
    notes: str = ""


class CaseIndex(CaseSummary):
    expected_flags: list[str]
    ground_truth_file: str


class SplitCounts(StrictModel):
    eval: int = Field(ge=0)
    held_out: int = Field(ge=0)


class CorpusIndex(StrictModel):
    schema_version: int = 1
    as_of: date
    watermark: Literal["SYNTHETIC"]
    carrier_count: int = Field(ge=0)
    case_count: int = Field(ge=0)
    split_counts: SplitCounts
    cases: list[CaseIndex]


__all__ = [
    "BaseRecord",
    "BrokerBondRecord",
    "CaseGroundTruth",
    "CaseIndex",
    "CaseSummary",
    "Citation",
    "CorpusIndex",
    "DocumentEntry",
    "FieldTruth",
    "FlagTruth",
    "PriorInvoiceLine",
    "PriorInvoiceRecord",
    "RecordEntry",
    "RecordPayload",
    "SplitCounts",
    "UccFilingRecord",
    "WatchlistRecord",
]
