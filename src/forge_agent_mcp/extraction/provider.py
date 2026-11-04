"""Provider-backed structured extraction with one retry.

The provider returns a schema generated from the field registry. Every citation
is verified against the source PDF. A schema or citation failure is retried once
with the rejection reason; a second failure becomes ``needs_more_info``. A
transport failure is not a schema retry and never produces a guessed value.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, create_model

from ..corpus.vocab import StrictModel
from ..providers.base import (
    ChatMessage,
    ChatProvider,
    ProviderError,
    ProviderSchemaError,
)
from .citations import CitationError, DocumentText
from .deterministic import extract_document
from .fields import FieldDefinition, field_definitions
from .layout import evidence_text
from .models import (
    Classification,
    DocumentExtraction,
    DocumentType,
    ExtractedValue,
    ExtractionCode,
    unclassified_document,
)

PROVIDER_ATTEMPTS = 2

SYSTEM_PROMPT = (
    "You extract fields from a synthetic freight-underwriting document. "
    "Return JSON matching the supplied schema. Copy each value verbatim from the "
    "document; never infer, normalise, repair or guess. Each value needs a citation: "
    "the document id, the 1-based page, the bbox [x0, y0, x1, y1] in PDF points "
    "(top-left origin) that contains the quote, and the exact source quote. "
    "The quote must contain the value. Each page below lists lines as "
    "[x0,y0,x1,y1] text; use those coordinates for the citation. "
    "If a required field is not present, omit it rather than inventing a value."
)


class OutputValidationError(ValueError):
    """A provider response was structurally valid but its citations were not."""

    def __init__(self, code: ExtractionCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _schema_name(document_type: str) -> str:
    return "".join(part.title() for part in document_type.split("_")) + "Extraction"


@cache
def extraction_schema(document_type: DocumentType) -> type[BaseModel]:
    definitions = field_definitions(document_type)
    if not definitions:
        raise ValueError(f"no field schema for document type {document_type!r}")
    fields: dict[str, Any] = {}
    for definition in definitions:
        annotation: Any = ExtractedValue if definition.required else ExtractedValue | None
        fields[definition.key] = (annotation, ...) if definition.required else (annotation, None)
    return create_model(_schema_name(document_type), __base__=StrictModel, **fields)


def _messages(
    document: DocumentText,
    classification: Classification,
    feedback: str | None = None,
) -> list[ChatMessage]:
    user = (
        f"Document ID: {classification.document_id}\n"
        f"Document type: {classification.document_type}\n"
        f"Title: {classification.title}\n\n"
        f"{evidence_text(document)}"
    )
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=user),
    ]
    if feedback:
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    f"The previous response was rejected: {feedback}. "
                    "Return a corrected JSON object that follows the schema and cites "
                    "the exact source text."
                ),
            )
        )
    return messages


def _validate_result(
    result: BaseModel,
    definitions: tuple[FieldDefinition, ...],
    document: DocumentText,
    classification: Classification,
) -> dict[str, ExtractedValue]:
    fields: dict[str, ExtractedValue] = {}
    errors: list[str] = []
    for definition in definitions:
        value = getattr(result, definition.key, None)
        if value is None:
            if definition.required:
                errors.append(f"{definition.key}: required field is missing")
            continue
        if not isinstance(value, ExtractedValue):
            errors.append(f"{definition.key}: value is not an extracted value")
            continue
        if value.citation.document_id != classification.document_id:
            errors.append(
                f"{definition.key}: citation names {value.citation.document_id!r}, "
                f"expected {classification.document_id!r}"
            )
            continue
        try:
            document.verify(value.citation, value.value)
        except CitationError as exc:
            errors.append(f"{definition.key}: {exc}")
            continue
        fields[definition.key] = value
    if errors:
        raise OutputValidationError("invalid_citation", "; ".join(errors))
    return fields


def _fallback_to_deterministic(
    path: str | Path,
    classification: Classification,
    *,
    code: ExtractionCode,
    message: str,
) -> DocumentExtraction:
    fallback = extract_document(path, classification)
    if fallback.status == "extracted":
        return fallback.model_copy(update={"fallback_used": True})
    return DocumentExtraction(
        classification=classification,
        status="needs_more_info",
        fields=fallback.fields,
        error=(
            f"provider extraction failed: {message}; deterministic fallback failed: "
            f"{fallback.error or 'unknown error'}"
        ),
        error_code=code,
        fallback_used=True,
    )


def extract_with_provider(
    provider: ChatProvider,
    path: str | Path,
    classification: Classification,
) -> DocumentExtraction:
    definitions = field_definitions(classification.document_type)
    if not definitions:
        return unclassified_document(
            classification,
            f"no field schema for document type {classification.document_type!r}",
        )

    document = DocumentText.load(path)
    schema = extraction_schema(classification.document_type)
    messages = _messages(document, classification)
    last_code: ExtractionCode = "schema_error"
    last_message = "provider did not return a usable extraction"

    for _attempt in range(PROVIDER_ATTEMPTS):
        try:
            result = provider.complete_structured(messages, schema, temperature=0.0)
            fields = _validate_result(result, definitions, document, classification)
            return DocumentExtraction(
                classification=classification,
                status="extracted",
                fields=fields,
            )
        except ProviderSchemaError as exc:
            last_code = "schema_error"
            last_message = str(exc)
        except OutputValidationError as exc:
            last_code = exc.code
            last_message = str(exc)
        except ProviderError as exc:
            return _fallback_to_deterministic(
                path,
                classification,
                code="provider_error",
                message=str(exc),
            )
        messages = _messages(document, classification, feedback=last_message)

    return _fallback_to_deterministic(
        path,
        classification,
        code=last_code,
        message=f"{last_message} (after {PROVIDER_ATTEMPTS} attempts)",
    )


__all__ = [
    "OutputValidationError",
    "PROVIDER_ATTEMPTS",
    "extract_with_provider",
    "extraction_schema",
]
