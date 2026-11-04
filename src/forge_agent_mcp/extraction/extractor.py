"""Case-level extraction: classify each packet document, extract fields, report.

The report is the hand-off to the later rule engine and gateway. It contains only
classified documents, extracted values with citations, missing packet documents
and stable issue codes; it never contains ground-truth labels.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from ..corpus.manifest import INVOICE_DOCUMENTS, ONBOARDING_DOCUMENTS
from ..corpus.models import CorpusIndex
from ..providers.base import ChatProvider
from .classify import ClassificationError, classify_document
from .deterministic import extract_document
from .models import (
    CaseExtraction,
    Classification,
    DocumentExtraction,
    ExtractionIssue,
)

CORPUS_INDEX = "corpus.json"


class ExtractionError(RuntimeError):
    """A case or corpus could not be read for extraction."""


def load_corpus_index(corpus_dir: str | Path) -> CorpusIndex:
    root = Path(corpus_dir)
    path = root / CORPUS_INDEX
    if not path.is_file():
        raise ExtractionError(f"corpus index not found at {path}; generate it with `make corpus`")
    try:
        return CorpusIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ExtractionError(f"could not read corpus index {path}: {exc}") from exc


def _missing_documents(case_type: str, document_types: set[str]) -> list[str]:
    required = ONBOARDING_DOCUMENTS if case_type == "onboarding" else INVOICE_DOCUMENTS
    return sorted(required - document_types)


def _unknown(document_id: str, title: str, filename: str) -> Classification:
    return Classification(
        document_id=document_id,
        document_type="unknown",
        title=title,
        filename=filename,
        confidence=0.0,
    )


def _extract_provider_document(
    provider: ChatProvider,
    path: Path,
    classification: Classification,
) -> DocumentExtraction:
    from .provider import extract_with_provider

    return extract_with_provider(provider, path, classification)


def extract_case(
    case_id: str,
    corpus_dir: str | Path,
    *,
    provider: ChatProvider | None = None,
) -> CaseExtraction:
    root = Path(corpus_dir)
    index = load_corpus_index(root)
    case = next((item for item in index.cases if item.case_id == case_id), None)
    if case is None:
        raise ExtractionError(f"unknown case {case_id!r} in {root / CORPUS_INDEX}")

    documents: list[DocumentExtraction] = []
    issues: list[ExtractionIssue] = []

    for entry in case.documents:
        path = root / "cases" / case_id / entry.filename
        if not path.is_file():
            classification = _unknown(entry.document_id, entry.title, entry.filename)
            extraction = DocumentExtraction(
                classification=classification,
                status="needs_more_info",
                error=f"document file is missing: {entry.filename}",
                error_code="document_missing",
            )
        else:
            try:
                classification = classify_document(
                    path,
                    document_id=entry.document_id,
                    title=entry.title,
                    filename=entry.filename,
                )
            except ClassificationError as exc:
                classification = _unknown(entry.document_id, entry.title, entry.filename)
                extraction = DocumentExtraction(
                    classification=classification,
                    status="needs_more_info",
                    error=str(exc),
                    error_code="unclassified_document",
                )
            else:
                if provider is None:
                    extraction = extract_document(path, classification)
                else:
                    extraction = _extract_provider_document(provider, path, classification)

        documents.append(extraction)
        if extraction.error_code is not None:
            issues.append(
                ExtractionIssue(
                    document_id=extraction.classification.document_id,
                    code=extraction.error_code,
                    message=extraction.error or extraction.error_code,
                )
            )

    status = (
        "needs_more_info"
        if any(document.status == "needs_more_info" for document in documents)
        else "extracted"
    )
    return CaseExtraction(
        case_id=case.case_id,
        case_type=case.case_type,
        provider="deterministic" if provider is None else provider.name,
        status=status,
        as_of=index.as_of.isoformat(),
        documents=documents,
        missing_documents=_missing_documents(
            case.case_type, {entry.document_type for entry in case.documents}
        ),
        issues=issues,
    )


__all__ = ["ExtractionError", "extract_case", "load_corpus_index"]
