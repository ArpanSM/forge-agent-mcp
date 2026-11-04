"""Deterministic field extraction for the known synthetic PDF templates.

This is the offline path used by tests and the demo. It reads label/value pairs
from the rendered pages, builds a bbox from the value words and then verifies
each citation with the same checker used for model output.
"""

from __future__ import annotations

from pathlib import Path

from .citations import CitationError, DocumentText, Word
from .fields import FieldDefinition, field_definitions
from .layout import Line, page_lines
from .models import Citation, Classification, DocumentExtraction, ExtractedValue

LABEL_MAX_X = 175.0
VALUE_MIN_X = 180.0
BBOX_PADDING = 1.0


def _matches_label(line: Line, label: str) -> bool:
    if line.left >= LABEL_MAX_X:
        return False
    return line.text == label or line.text.startswith(f"{label} ")


def _value_words(lines: list[Line], index: int) -> list[Word]:
    line = lines[index]
    words = [word for word in line.words if word.bbox[0] >= VALUE_MIN_X]
    if not words:
        return []

    for next_index in range(index + 1, len(lines)):
        candidate = lines[next_index]
        if candidate.page != line.page or candidate.left < VALUE_MIN_X:
            break
        words.extend(candidate.words)
    return words


def _bbox_for(words: list[Word], page_width: float, page_height: float) -> tuple[float, ...]:
    x0 = min(word.bbox[0] for word in words) - BBOX_PADDING
    y0 = min(word.bbox[1] for word in words) - BBOX_PADDING
    x1 = max(word.bbox[2] for word in words) + BBOX_PADDING
    y1 = max(word.bbox[3] for word in words) + BBOX_PADDING
    return (
        round(max(0.0, x0), 2),
        round(max(0.0, y0), 2),
        round(min(page_width, x1), 2),
        round(min(page_height, y1), 2),
    )


def _extract_field(
    document: DocumentText,
    lines: list[Line],
    definition: FieldDefinition,
    document_id: str,
) -> ExtractedValue | None:
    for index, line in enumerate(lines):
        if not _matches_label(line, definition.label):
            continue
        words = _value_words(lines, index)
        if not words:
            continue
        quote = " ".join(word.text for word in words)
        page = document.pages[line.page - 1]
        bbox = _bbox_for(words, page.width, page.height)
        citation = Citation(
            document_id=document_id,
            page=line.page,
            bbox=bbox,
            quote=quote,
        )
        return ExtractedValue(value=quote, citation=citation)
    return None


def extract_document(path: str | Path, classification: Classification) -> DocumentExtraction:
    definitions = field_definitions(classification.document_type)
    if not definitions:
        return DocumentExtraction(
            classification=classification,
            status="needs_more_info",
            error=f"no field schema for document type {classification.document_type!r}",
            error_code="unclassified_document",
        )

    document = DocumentText.load(path)
    lines = page_lines(document)
    fields: dict[str, ExtractedValue] = {}
    missing: list[str] = []
    invalid: list[str] = []

    for definition in definitions:
        extracted = _extract_field(document, lines, definition, classification.document_id)
        if extracted is None:
            if definition.required:
                missing.append(definition.key)
            continue
        try:
            document.verify(extracted.citation, extracted.value)
        except CitationError:
            invalid.append(definition.key)
            continue
        fields[definition.key] = extracted

    errors: list[str] = []
    if missing:
        errors.append("missing required field(s): " + ", ".join(missing))
    if invalid:
        errors.append("citation verification failed for: " + ", ".join(invalid))
    if errors:
        code = "missing_field" if missing else "invalid_citation"
        return DocumentExtraction(
            classification=classification,
            status="needs_more_info",
            fields=fields,
            error="; ".join(errors),
            error_code=code,
        )
    return DocumentExtraction(
        classification=classification,
        status="extracted",
        fields=fields,
    )


__all__ = ["extract_document"]
