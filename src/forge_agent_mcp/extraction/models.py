"""Public extraction contracts: citations, values, reports and result envelopes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field, field_validator

from ..corpus.vocab import CaseType, StrictModel

KnownDocumentType = Literal[
    "application_kyc",
    "w9",
    "certificate_of_insurance",
    "bank_letter",
    "mc_authority",
    "rate_confirmation",
    "bill_of_lading",
    "proof_of_delivery",
    "invoice",
]
DocumentType = KnownDocumentType | Literal["unknown"]
ExtractionStatus = Literal["extracted", "needs_more_info"]
ExtractionCode = Literal[
    "unclassified_document",
    "missing_field",
    "schema_error",
    "invalid_citation",
    "document_missing",
    "document_unreadable",
    "provider_error",
]
ExtractionErrorCode = Literal["unknown_case", "corpus_not_found", "corpus_invalid"]
Classifier = Literal["provider", "keywords"]

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
    classifier: Classifier = "keywords"


class DocumentExtraction(StrictModel):
    classification: Classification
    status: ExtractionStatus
    fields: dict[str, ExtractedValue] = Field(default_factory=dict)
    error: str | None = None
    error_code: ExtractionCode | None = None
    fallback_used: bool = False


class ExtractionIssue(StrictModel):
    document_id: str | None = None
    code: ExtractionCode
    message: str


class CaseExtraction(StrictModel):
    schema_version: int = 1
    case_id: str
    case_type: CaseType
    provider: str
    status: ExtractionStatus
    as_of: str | None = None
    documents: list[DocumentExtraction]
    missing_documents: list[str] = Field(default_factory=list)
    issues: list[ExtractionIssue] = Field(default_factory=list)


class CaseExtractionResult(StrictModel):
    """Result envelope for the case-extraction operation.

    Business failures (unknown case, missing corpus) return ``ok: false`` with a
    stable code instead of raising through the library or CLI.
    """

    ok: bool
    error_code: ExtractionErrorCode | None = None
    message: str | None = None
    data: CaseExtraction | None = None


def unknown_classification(
    document_id: str,
    title: str,
    filename: str,
) -> Classification:
    return Classification(
        document_id=document_id,
        document_type="unknown",
        title=title,
        filename=filename,
        confidence=0.0,
        classifier="keywords",
    )


def needs_more_info_document(
    classification: Classification,
    message: str,
    code: ExtractionCode,
) -> DocumentExtraction:
    return DocumentExtraction(
        classification=classification,
        status="needs_more_info",
        error=message,
        error_code=code,
    )


def unclassified_document(classification: Classification, message: str) -> DocumentExtraction:
    return needs_more_info_document(classification, message, "unclassified_document")


def overall_status(documents: Sequence[DocumentExtraction]) -> ExtractionStatus:
    if any(document.status == "needs_more_info" for document in documents):
        return "needs_more_info"
    return "extracted"


__all__ = [
    "BBox",
    "CaseExtraction",
    "CaseExtractionResult",
    "Citation",
    "Classification",
    "Classifier",
    "DocumentExtraction",
    "DocumentType",
    "ExtractedValue",
    "ExtractionCode",
    "ExtractionErrorCode",
    "ExtractionIssue",
    "ExtractionStatus",
    "KnownDocumentType",
    "needs_more_info_document",
    "overall_status",
    "unclassified_document",
    "unknown_classification",
]
