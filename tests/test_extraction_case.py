from __future__ import annotations

from pathlib import Path

from forge_agent_mcp.extraction.citations import verify_citation
from forge_agent_mcp.extraction.extractor import extract_case
from forge_agent_mcp.extraction.models import CaseExtraction
from forge_agent_mcp.providers.fake import FakeProvider


def successful_report(result) -> CaseExtraction:
    assert result.ok is True, result.message
    assert result.error_code is None
    assert result.data is not None
    return result.data


def test_extract_case_classifies_documents_and_keeps_citations(generated_corpus) -> None:
    _manifest, result = generated_corpus
    case_id = "case_onb_ridgeline"
    truth = result.cases[case_id]

    report = successful_report(extract_case(case_id, result.root))

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
        assert document.fallback_used is False
        assert document.fields
        path = result.root / "cases" / case_id / document.classification.filename
        for value in document.fields.values():
            assert value.citation.document_id == document.classification.document_id
            verify_citation(path, value.citation, value.value)

    assert CaseExtraction.model_validate_json(report.model_dump_json()) == report


def test_extract_case_reports_missing_packet_documents_without_failing(generated_corpus) -> None:
    _manifest, result = generated_corpus

    report = successful_report(extract_case("case_inv_sagebrush", result.root))

    assert report.status == "extracted"
    assert report.missing_documents == ["proof_of_delivery"]
    assert all(document.status == "extracted" for document in report.documents)


def test_extract_case_returns_an_envelope_for_an_unknown_case(generated_corpus) -> None:
    _manifest, result = generated_corpus

    outcome = extract_case("case_does_not_exist", result.root)

    assert outcome.ok is False
    assert outcome.error_code == "unknown_case"
    assert "unknown case" in (outcome.message or "")
    assert outcome.data is None


def test_extract_case_returns_an_envelope_for_a_missing_corpus(tmp_path: Path) -> None:
    outcome = extract_case("case_onb_ridgeline", tmp_path / "absent")

    assert outcome.ok is False
    assert outcome.error_code == "corpus_not_found"
    assert "corpus" in (outcome.message or "")
    assert outcome.data is None


def test_provider_failure_falls_back_for_the_whole_case(generated_corpus) -> None:
    _manifest, result = generated_corpus
    provider = FakeProvider(structured={"document_type": "not_a_document"})

    report = successful_report(extract_case("case_onb_ridgeline", result.root, provider=provider))

    assert report.provider == "fake"
    assert report.status == "extracted"
    assert report.issues == []
    assert all(document.fallback_used for document in report.documents)
    assert all(document.status == "extracted" for document in report.documents)


def test_extract_case_uses_provider_output_when_it_is_valid(
    generated_corpus, provider_payload
) -> None:
    _manifest, result = generated_corpus
    truth = result.cases["case_onb_ridgeline"]
    provider = FakeProvider()
    for document in truth.documents:
        provider.queue_structured({"document_type": document.document_type, "confidence": 0.99})
        provider.queue_structured(provider_payload(truth, document))

    report = successful_report(extract_case("case_onb_ridgeline", result.root, provider=provider))

    assert report.status == "extracted"
    assert report.provider == "fake"
    assert report.issues == []
    assert len(provider.calls) == 10
    assert all(document.fields for document in report.documents)
    assert all(document.classification.classifier == "provider" for document in report.documents)
    assert all(document.fallback_used is False for document in report.documents)
    assert report.documents[0].fields["legal_name"].value == "Ridgeline Haulage LLC"
