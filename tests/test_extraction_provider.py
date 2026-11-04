from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pymupdf

from forge_agent_mcp.extraction.citations import verify_citation
from forge_agent_mcp.extraction.classify import classify_document
from forge_agent_mcp.extraction.models import Classification
from forge_agent_mcp.extraction.provider import extract_with_provider
from forge_agent_mcp.providers.base import ProviderTransportError
from forge_agent_mcp.providers.fake import FakeProvider


def corpus_document(generated_corpus, case_id: str, document_type: str):
    _manifest, result = generated_corpus
    truth = result.cases[case_id]
    document = next(item for item in truth.documents if item.document_type == document_type)
    path = result.root / "cases" / case_id / document.filename
    classification = classify_document(
        path,
        document_id=document.document_id,
        title=document.title,
        filename=document.filename,
    )
    return truth, document, path, classification


def unparseable_application(tmp_path: Path) -> tuple[Path, Classification]:
    path = tmp_path / "blank.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Nothing relevant is written on this page.")
    pdf.save(path)
    pdf.close()
    return path, Classification(
        document_id="DOC-BLANK-APP",
        document_type="application_kyc",
        title="Carrier Application / KYC",
        filename="documents/APP.pdf",
        confidence=1.0,
    )


def test_provider_extraction_accepts_valid_structured_output(
    generated_corpus, provider_payload
) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = provider_payload(truth, document)
    provider = FakeProvider()
    provider.queue_structured(payload)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert extraction.error_code is None
    assert extraction.fallback_used is False
    assert set(extraction.fields) == set(payload)
    assert extraction.fields["legal_name"].value == "Ridgeline Haulage LLC"
    assert extraction.fields["legal_name"].confidence == 0.9
    assert len(provider.calls) == 1
    assert provider.calls[0].schema_name == "ApplicationKycExtraction"
    for field in extraction.fields.values():
        verify_citation(path, field.citation, field.value)


def test_provider_schema_failure_retries_once_and_then_succeeds(
    generated_corpus, provider_payload
) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = provider_payload(truth, document)
    provider = FakeProvider()
    provider.queue_structured({"legal_name": {"value": "incomplete schema"}})
    provider.queue_structured(payload)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert extraction.fields["ein"].value == "00-0000101"
    assert len(provider.calls) == 2


def test_two_schema_failures_fall_back_to_the_deterministic_parser(
    generated_corpus,
) -> None:
    _truth, _document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    provider = FakeProvider()
    provider.queue_structured({"legal_name": {"value": "incomplete schema"}})
    provider.queue_structured({"legal_name": {"value": "still incomplete"}})

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert extraction.error_code is None
    assert extraction.fallback_used is True
    assert extraction.fields["legal_name"].value == "Ridgeline Haulage LLC"
    assert len(provider.calls) == 2


def test_schema_failures_still_need_info_when_the_fallback_also_fails(tmp_path: Path) -> None:
    path, classification = unparseable_application(tmp_path)
    provider = FakeProvider()
    provider.queue_structured({"legal_name": {"value": "incomplete schema"}})
    provider.queue_structured({"legal_name": {"value": "still incomplete"}})

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "needs_more_info"
    assert extraction.error_code == "schema_error"
    assert extraction.fallback_used is True
    assert extraction.error is not None
    assert "deterministic fallback failed" in extraction.error
    assert len(provider.calls) == 2


def test_provider_citation_failure_is_retried_and_then_falls_back(
    generated_corpus, provider_payload
) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = provider_payload(truth, document)
    bad = deepcopy(payload)
    bad["legal_name"]["citation"]["bbox"] = [0.0, 0.0, 1.0, 1.0]
    provider = FakeProvider()
    provider.queue_structured(bad)
    provider.queue_structured(payload)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert len(provider.calls) == 2


def test_two_citation_failures_fall_back_to_the_deterministic_parser(
    generated_corpus, provider_payload
) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = provider_payload(truth, document)
    bad = deepcopy(payload)
    bad["legal_name"]["citation"]["quote"] = "A value that is not in the document"
    bad["legal_name"]["value"] = "A value that is not in the document"
    provider = FakeProvider()
    provider.queue_structured(bad)
    provider.queue_structured(bad)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert extraction.fallback_used is True
    assert len(provider.calls) == 2


def test_provider_transport_error_uses_the_deterministic_fallback(generated_corpus) -> None:
    _truth, _document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )

    class OfflineProvider(FakeProvider):
        def complete_structured(self, messages, schema, **kwargs):  # type: ignore[no-untyped-def]
            raise ProviderTransportError("provider is offline")

    extraction = extract_with_provider(OfflineProvider(), path, classification)

    assert extraction.status == "extracted"
    assert extraction.fallback_used is True


def test_provider_error_still_needs_info_when_the_fallback_also_fails(tmp_path: Path) -> None:
    path, classification = unparseable_application(tmp_path)

    class OfflineProvider(FakeProvider):
        def complete_structured(self, messages, schema, **kwargs):  # type: ignore[no-untyped-def]
            raise ProviderTransportError("provider is offline")

    extraction = extract_with_provider(OfflineProvider(), path, classification)

    assert extraction.status == "needs_more_info"
    assert extraction.error_code == "provider_error"
    assert extraction.fallback_used is True
    assert "offline" in (extraction.error or "")


def test_provider_extraction_rejects_a_citation_from_another_document(
    generated_corpus, provider_payload
) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = provider_payload(truth, document)
    payload["legal_name"]["citation"]["document_id"] = "DOC-OTHER-APP"
    provider = FakeProvider()
    provider.queue_structured(payload)
    provider.queue_structured(payload)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert extraction.fallback_used is True
