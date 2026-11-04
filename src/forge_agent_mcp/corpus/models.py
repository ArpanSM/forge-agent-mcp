"""Generated corpus models: index, ground truth and citations."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class GeneratedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(GeneratedModel):
    document_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    record_path: str | None = None
    missing_document_type: str | None = None


class FieldTruth(GeneratedModel):
    field: str
    value: str
    citation: Citation


class FlagTruth(GeneratedModel):
    code: str
    severity: Severity
    rule: str
    evidence: Citation
    details: str


class DocumentEntry(GeneratedModel):
    document_id: str
    document_type: str
    title: str
    filename: str
    pages: int = Field(ge=1)
    content_hash: str
    sha256: str


class RecordEntry(GeneratedModel):
    record_id: str
    record_type: str
    title: str
    filename: str
    content_hash: str
    sha256: str


class CaseGroundTruth(GeneratedModel):
    case_id: str
    case_type: CaseType
    carrier_id: str
    split: Split
    category: Category
    archetypes: list[str]
    documents: list[DocumentEntry]
    structured_records: list[RecordEntry]
    expected_fields: list[FieldTruth]
    expected_records: dict[str, Any]
    expected_flags: list[FlagTruth]
    expected_recommendation: Recommendation
    expected_decision: Decision
    expected_conditions: list[str]
    notes: str = ""


class CaseIndex(GeneratedModel):
    case_id: str
    case_type: CaseType
    carrier_id: str
    split: Split
    category: Category
    archetypes: list[str]
    documents: list[DocumentEntry]
    structured_records: list[RecordEntry]
    expected_flags: list[str]
    expected_recommendation: Recommendation
    expected_decision: Decision
    expected_conditions: list[str]
    ground_truth_file: str


class SplitCounts(GeneratedModel):
    eval: int = Field(ge=0)
    held_out: int = Field(ge=0)


class CorpusIndex(GeneratedModel):
    schema_version: int = 1
    as_of: date
    watermark: Literal["SYNTHETIC"]
    carrier_count: int = Field(ge=0)
    case_count: int = Field(ge=0)
    split_counts: SplitCounts
    cases: list[CaseIndex]
