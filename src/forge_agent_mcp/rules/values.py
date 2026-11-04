"""Small pure helpers for reading extraction values and normalising evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from ..extraction.models import (
    CaseExtraction,
    DocumentExtraction,
    ExtractedValue,
    KnownDocumentType,
)
from .models import Evidence

MONEY_RE = re.compile(r"^\$?(?P<amount>-?\d[\d,]*)(?:\.(?P<cents>\d{1,2}))?$")
WEIGHT_RE = re.compile(r"^(?P<amount>\d[\d,]*)\s*lbs?$", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ParsedValue[T]:
    extracted: ExtractedValue
    value: T


@dataclass(frozen=True)
class Documents:
    by_type: dict[KnownDocumentType, DocumentExtraction]

    @classmethod
    def from_extraction(cls, extraction: CaseExtraction) -> Documents:
        by_type: dict[KnownDocumentType, DocumentExtraction] = {}
        for document in extraction.documents:
            document_type = document.classification.document_type
            if document_type == "unknown" or document_type in by_type:
                continue
            by_type[document_type] = document
        return cls(by_type=by_type)

    def get(self, document_type: KnownDocumentType) -> DocumentExtraction | None:
        return self.by_type.get(document_type)

    def field(self, document_type: KnownDocumentType, key: str) -> ExtractedValue | None:
        document = self.get(document_type)
        if document is None:
            return None
        return document.fields.get(key)

    def value(self, document_type: KnownDocumentType, key: str) -> str | None:
        extracted = self.field(document_type, key)
        return extracted.value if extracted is not None else None


def evidence_for(value: ExtractedValue, document_type: KnownDocumentType) -> Evidence:
    citation = value.citation
    return Evidence(
        document_id=citation.document_id,
        document_type=document_type,
        page=citation.page,
        bbox=citation.bbox,
        quote=citation.quote,
    )


def normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def normalize_identifier(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def parse_money(value: str | None) -> Decimal | None:
    if value is None:
        return None
    match = MONEY_RE.match(value.strip())
    if match is None:
        return None
    amount = match.group("amount").replace(",", "")
    cents = (match.group("cents") or "0").ljust(2, "0")
    try:
        return Decimal(f"{amount}.{cents}")
    except InvalidOperation:
        return None


def parse_weight(value: str | None) -> int | None:
    if value is None:
        return None
    match = WEIGHT_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group("amount").replace(",", ""))


def parse_date(value: str | None) -> date | None:
    if value is None or DATE_RE.match(value.strip()) is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def date_value(
    documents: Documents,
    document_type: KnownDocumentType,
    key: str,
) -> ParsedValue[date] | None:
    extracted = documents.field(document_type, key)
    if extracted is None:
        return None
    parsed = parse_date(extracted.value)
    return ParsedValue(extracted, parsed) if parsed is not None else None


def money_value(
    documents: Documents,
    document_type: KnownDocumentType,
    key: str,
) -> ParsedValue[Decimal] | None:
    extracted = documents.field(document_type, key)
    if extracted is None:
        return None
    parsed = parse_money(extracted.value)
    return ParsedValue(extracted, parsed) if parsed is not None else None


def weight_value(
    documents: Documents,
    document_type: KnownDocumentType,
    key: str,
) -> ParsedValue[int] | None:
    extracted = documents.field(document_type, key)
    if extracted is None:
        return None
    parsed = parse_weight(extracted.value)
    return ParsedValue(extracted, parsed) if parsed is not None else None


def format_money(value: Decimal) -> str:
    return f"${value:,.2f}"


__all__ = [
    "Documents",
    "ParsedValue",
    "date_value",
    "evidence_for",
    "format_money",
    "money_value",
    "normalize_identifier",
    "normalize_name",
    "parse_date",
    "parse_money",
    "parse_weight",
    "weight_value",
]
