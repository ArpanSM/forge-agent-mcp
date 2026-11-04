"""Case-level extraction: classify each packet document, extract fields, report.

The report is the hand-off to the later rule engine and gateway. It contains only
classified documents, extracted values with citations, missing packet documents
and stable issue codes; it never contains ground-truth labels.

Business failures (absent corpus, unknown case) return a result envelope with
``ok: false`` and a stable code instead of raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import ValidationError

from ..corpus.manifest import INVOICE_DOCUMENTS, ONBOARDING_DOCUMENTS
from ..corpus.models import CorpusIndex
from ..corpus.vocab import CaseType
from ..providers.base import ChatProvider
from .citations import DocumentReadError
from .classify import classify_document, classify_document_with_provider
from .deterministic import extract_document
from .models import (
    CaseExtraction,
    CaseExtractionResult,
    Classification,
    DocumentExtraction,
    DocumentType,
    ExtractionErrorCode,
    ExtractionIssue,
    needs_more_info_document,
    overall_status,
    unknown_classification,
)
from .provider import extract_with_provider

CORPUS_INDEX = "corpus.json"

REQUIRED_DOCUMENTS: dict[CaseType, frozenset[DocumentType]] = {
    "onboarding": frozenset(cast(DocumentType, name) for name in ONBOARDING_DOCUMENTS),
    "invoice": frozenset(cast(DocumentType, name) for name in INVOICE_DOCUMENTS),
}


class ExtractionError(RuntimeError):
    """A case or corpus could not be read for extraction."""

    def __init__(self, code: ExtractionErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _load_corpus_index(corpus_dir: Path) -> CorpusIndex:
    path = corpus_dir / CORPUS_INDEX
    if not path.is_file():
        raise ExtractionError(
            "corpus_not_found",
            f"corpus index not found at {path}; generate it with `make corpus`",
        )
    try:
        return CorpusIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ExtractionError(
            "corpus_invalid",
            f"could not read corpus index {path}: {exc}",
        ) from exc


def _missing_documents(
    case_type: CaseType,
    document_types: set[DocumentType],
) -> list[str]:
    return sorted(REQUIRED_DOCUMENTS[case_type] - document_types)


def _document_missing(document_id: str, title: str, filename: str) -> DocumentExtraction:
    return needs_more_info_document(
        unknown_classification(document_id, title, filename),
        f"document file is missing: {filename}",
        "document_missing",
    )


def _document_unreadable(
    document_id: str,
    title: str,
    filename: str,
    message: str,
) -> DocumentExtraction:
    return needs_more_info_document(
        unknown_classification(document_id, title, filename),
        message,
        "document_unreadable",
    )


def extract_case(
    case_id: str,
    corpus_dir: str | Path,
    *,
    provider: ChatProvider | None = None,
) -> CaseExtractionResult:
    root = Path(corpus_dir)
    try:
        index = _load_corpus_index(root)
    except ExtractionError as exc:
        return CaseExtractionResult(ok=False, error_code=exc.code, message=str(exc))

    case = next((item for item in index.cases if item.case_id == case_id), None)
    if case is None:
        return CaseExtractionResult(
            ok=False,
            error_code="unknown_case",
            message=f"unknown case {case_id!r} in {root / CORPUS_INDEX}",
        )

    documents: list[DocumentExtraction] = []
    issues: list[ExtractionIssue] = []

    for entry in case.documents:
        path = root / "cases" / case_id / entry.filename
        try:
            if not path.is_file():
                extraction = _document_missing(entry.document_id, entry.title, entry.filename)
            else:
                classification: Classification
                if provider is None:
                    classification = classify_document(
                        path,
                        document_id=entry.document_id,
                        title=entry.title,
                        filename=entry.filename,
                    )
                else:
                    classification = classify_document_with_provider(
                        provider,
                        path,
                        document_id=entry.document_id,
                        title=entry.title,
                        filename=entry.filename,
                    )
                if provider is None:
                    extraction = extract_document(path, classification)
                else:
                    extraction = extract_with_provider(provider, path, classification)
        except DocumentReadError as exc:
            extraction = _document_unreadable(
                entry.document_id, entry.title, entry.filename, str(exc)
            )

        documents.append(extraction)
        if extraction.error_code is not None:
            issues.append(
                ExtractionIssue(
                    document_id=extraction.classification.document_id,
                    code=extraction.error_code,
                    message=extraction.error or extraction.error_code,
                )
            )

    document_types = {cast(DocumentType, entry.document_type) for entry in case.documents}
    report = CaseExtraction(
        case_id=case.case_id,
        case_type=case.case_type,
        provider="deterministic" if provider is None else provider.name,
        status=overall_status(documents),
        as_of=index.as_of.isoformat(),
        documents=documents,
        missing_documents=_missing_documents(case.case_type, document_types),
        issues=issues,
    )
    return CaseExtractionResult(ok=True, data=report)


__all__ = ["ExtractionError", "extract_case"]
