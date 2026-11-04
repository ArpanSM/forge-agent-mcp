"""Public extraction contracts: citations, values and case reports."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from ..corpus.vocab import CaseType, StrictModel

DocumentType = Literal[
    "application_kyc",
    "w9",
    "certificate_of_insurance",
    "bank_letter",
    "mc_authority",
    "rate_confirmation",
    "bill_of_lading",
    "proof_of_delivery",
    "invoice",
    "unknown",
]

BBox = tuple[float, float, float, float]


class Citation(StrictModel):
    """The source location and verbatim quote for one extracted value.

    Bounding boxes use PDF page points with a top-left origin: ``[x0, y0, x1, y1]``.
    """

    document_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    bbox: BBox
    quote: str = Field(min_length=1)

    @field_validator("bbox")
    @classmethod
    def _bbox_is_ordered(cls, value: BBox) -> BBox:
        x0, y0, x1, y1 = value
        if x0 >= x1 or y0 >= y1:
            raise ValueError("bbox must satisfy x0 < x1 and y0 < y1")
        return value


class ExtractedValue(StrictModel):
    value: str = Field(min_length=1)
    citation: Citation
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Classification(StrictModel):
    document_id: str
    document_type: DocumentType
    title: str
    filename: str
    confidence: float = Field(ge=0.0, le=1.0)


class DocumentExtraction(StrictModel):
    classification: Classification
    status: Literal["extracted", "needs_more_info"]
    fields: dict[str, ExtractedValue] = Field(default_factory=dict)
    error: str | None = None


class ExtractionIssue(StrictModel):
    document_id: str | None = None
    code: Literal[
        "unclassified_document",
        "missing_field",
        "schema_error",
        "invalid_citation",
        "document_missing",
        "provider_error",
    ]
    message: str


class CaseExtraction(StrictModel):
    schema_version: int = 1
    case_id: str
    case_type: CaseType
    provider: str
    status: Literal["extracted", "needs_more_info"]
    as_of: str | None = None
    documents: list[DocumentExtraction]
    missing_documents: list[str] = Field(default_factory=list)
    issues: list[ExtractionIssue] = Field(default_factory=list)


__all__ = [
    "BBox",
    "CaseExtraction",
    "Citation",
    "Classification",
    "DocumentExtraction",
    "DocumentType",
    "ExtractedValue",
    "ExtractionIssue",
]
