"""Shared helpers for rule-engine tests: corpus fixtures in, review out."""

from __future__ import annotations

from pathlib import Path

from forge_agent_mcp.extraction.extractor import extract_case
from forge_agent_mcp.extraction.models import CaseExtraction
from forge_agent_mcp.rules.corpus import load_case_packet
from forge_agent_mcp.rules.engine import review
from forge_agent_mcp.rules.models import CaseReview


def build_review(corpus_root: Path, case_id: str) -> CaseReview:
    extraction = extract_case(case_id, corpus_root)
    assert extraction.ok is True, extraction.message
    assert extraction.data is not None
    packet = load_case_packet(case_id, corpus_root)
    return review(extraction.data, packet)


def review_corpus_case(generated_corpus, case_id: str) -> CaseReview:
    _manifest, result = generated_corpus
    return build_review(result.root, case_id)


def replace_field_value(
    extraction: CaseExtraction,
    document_type: str,
    key: str,
    value: str,
) -> CaseExtraction:
    """Return a copy with one extracted value changed; citations stay intact."""

    documents = []
    for document in extraction.documents:
        if document.classification.document_type != document_type:
            documents.append(document)
            continue
        extracted = document.fields[key]
        documents.append(
            document.model_copy(
                update={
                    "fields": {
                        **document.fields,
                        key: extracted.model_copy(
                            update={
                                "value": value,
                                "citation": extracted.citation.model_copy(
                                    update={"quote": value}
                                ),
                            }
                        ),
                    }
                }
            )
        )
    return extraction.model_copy(update={"documents": documents})
