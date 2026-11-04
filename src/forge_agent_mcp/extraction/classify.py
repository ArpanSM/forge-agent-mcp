"""Deterministic document classification from the page text.

Keywords are content signals, not file names or index metadata, so a renamed or
scanned document still classifies from what the page actually says.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .citations import normalize_space
from .models import Classification, DocumentType

TITLE_WEIGHT = 5.0
LABEL_WEIGHT = 1.0
MIN_SCORE = 3.0

CLASSIFICATION_TERMS: dict[DocumentType, tuple[tuple[str, float], ...]] = {
    "application_kyc": (
        ("carrier application", TITLE_WEIGHT),
        ("requested advance", LABEL_WEIGHT),
        ("requested reserve", LABEL_WEIGHT),
        ("owner email", LABEL_WEIGHT),
        ("formation date", LABEL_WEIGHT),
        ("dba", LABEL_WEIGHT),
    ),
    "w9": (
        ("form w-9", TITLE_WEIGHT),
        ("tax classification", LABEL_WEIGHT),
        ("business name", LABEL_WEIGHT),
        ("signed on", LABEL_WEIGHT),
        ("taxpayer identification", LABEL_WEIGHT),
    ),
    "certificate_of_insurance": (
        ("certificate of insurance", TITLE_WEIGHT),
        ("auto liability limit", LABEL_WEIGHT),
        ("cargo limit", LABEL_WEIGHT),
        ("general liability limit", LABEL_WEIGHT),
        ("additional insured", LABEL_WEIGHT),
        ("policy number", LABEL_WEIGHT),
    ),
    "bank_letter": (
        ("bank letter", TITLE_WEIGHT),
        ("account holder", LABEL_WEIGHT),
        ("routing number", LABEL_WEIGHT),
        ("account number", LABEL_WEIGHT),
        ("account opened on", LABEL_WEIGHT),
        ("letter date", LABEL_WEIGHT),
    ),
    "mc_authority": (
        ("mc authority", TITLE_WEIGHT),
        ("authority status", LABEL_WEIGHT),
        ("authority granted on", LABEL_WEIGHT),
        ("authority updated on", LABEL_WEIGHT),
        ("operating radius", LABEL_WEIGHT),
        ("safety rating", LABEL_WEIGHT),
    ),
    "rate_confirmation": (
        ("rate confirmation", TITLE_WEIGHT),
        ("rate confirmation number", LABEL_WEIGHT),
        ("payment terms", LABEL_WEIGHT),
        ("linehaul", LABEL_WEIGHT),
        ("fuel surcharge", LABEL_WEIGHT),
        ("accessorials", LABEL_WEIGHT),
    ),
    "bill_of_lading": (
        ("bill of lading", TITLE_WEIGHT),
        ("bol number", LABEL_WEIGHT),
        ("shipper", LABEL_WEIGHT),
        ("consignee", LABEL_WEIGHT),
        ("commodity", LABEL_WEIGHT),
    ),
    "proof_of_delivery": (
        ("proof of delivery", TITLE_WEIGHT),
        ("pod number", LABEL_WEIGHT),
        ("delivered on", LABEL_WEIGHT),
        ("delivered weight", LABEL_WEIGHT),
        ("signed by", LABEL_WEIGHT),
    ),
    "invoice": (
        ("freight invoice", TITLE_WEIGHT),
        ("invoice number", LABEL_WEIGHT),
        ("invoice date", LABEL_WEIGHT),
        ("due date", LABEL_WEIGHT),
        ("remit to", LABEL_WEIGHT),
        ("broker", LABEL_WEIGHT),
    ),
}


class ClassificationError(RuntimeError):
    """A document could not be read for classification."""


def read_document_text(path: str | Path) -> str:
    source = Path(path)
    try:
        with pymupdf.open(source) as pdf:
            return normalize_space("\n".join(page.get_text("text") for page in pdf))
    except (pymupdf.FileNotFoundError, RuntimeError) as exc:
        raise ClassificationError(f"could not read document {source}: {exc}") from exc


def score_document_types(text: str) -> dict[DocumentType, float]:
    normalized = text.lower()
    scores: dict[DocumentType, float] = {}
    for document_type, terms in CLASSIFICATION_TERMS.items():
        score = sum(weight for term, weight in terms if term in normalized)
        if score:
            scores[document_type] = score
    return scores


def classify_document(
    path: str | Path,
    *,
    document_id: str,
    title: str = "",
    filename: str = "",
) -> Classification:
    text = read_document_text(path)
    scores = score_document_types(text)
    if not scores:
        return Classification(
            document_id=document_id,
            document_type="unknown",
            title=title or Path(path).stem,
            filename=filename or Path(path).name,
            confidence=0.0,
        )

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < MIN_SCORE:
        return Classification(
            document_id=document_id,
            document_type="unknown",
            title=title or Path(path).stem,
            filename=filename or Path(path).name,
            confidence=0.0,
        )

    confidence = min(1.0, best_score / (best_score + runner_up + 2.0))
    return Classification(
        document_id=document_id,
        document_type=best_type,
        title=title or Path(path).stem,
        filename=filename or Path(path).name,
        confidence=round(confidence, 4),
    )


__all__ = [
    "ClassificationError",
    "classify_document",
    "read_document_text",
    "score_document_types",
]
