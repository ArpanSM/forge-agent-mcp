from __future__ import annotations

from pathlib import Path

import pytest

from forge_agent_mcp.extraction.citations import verify_citation
from forge_agent_mcp.extraction.extractor import ExtractionError, extract_case
from forge_agent_mcp.extraction.models import CaseExtraction


def test_extract_case_classifies_documents_and_keeps_citations(generated_corpus) -> None:
    _manifest, result = generated_corpus
    case_id = "case_onb_ridgeline"
    truth = result.cases[case_id]

    report = extract_case(case_id, result.root)

    assert report.case_id == case_id
    assert report.case_type == "onboarding"
    assert report.provider == "deterministic"
    assert report.status == "extracted"
    assert report.missing_documents == []
    assert [document.classification.document_type for document in report.documents] == [
        document.document_type for document in truth.documents
    ]

    for document in report.documents:
        assert document.status == "extracted"
        assert document.fields
        path = result.root / "cases" / case_id / document.classification.filename
        for value in document.fields.values():
            assert value.citation.document_id == document.classification.document_id
            verify_citation(path, value.citation, value.value)

    assert CaseExtraction.model_validate_json(report.model_dump_json()) == report


def test_extract_case_reports_missing_packet_documents_without_failing(generated_corpus) -> None:
    _manifest, result = generated_corpus

    report = extract_case("case_inv_sagebrush", result.root)

    assert report.status == "extracted"
    assert report.missing_documents == ["proof_of_delivery"]
    assert all(document.status == "extracted" for document in report.documents)


def test_extract_case_rejects_an_unknown_case(generated_corpus) -> None:
    _manifest, result = generated_corpus

    with pytest.raises(ExtractionError, match="unknown case"):
        extract_case("case_does_not_exist", result.root)


def test_extract_case_reports_a_missing_corpus(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="corpus"):
        extract_case("case_onb_ridgeline", tmp_path / "absent")
