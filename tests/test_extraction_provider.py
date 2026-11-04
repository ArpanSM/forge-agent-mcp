from __future__ import annotations

from copy import deepcopy
from typing import Any

from forge_agent_mcp.extraction.citations import verify_citation
from forge_agent_mcp.extraction.classify import classify_document
from forge_agent_mcp.extraction.provider import extract_with_provider
from forge_agent_mcp.providers.base import ProviderTransportError
from forge_agent_mcp.providers.fake import FakeProvider


def payload_from_truth(truth, document) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in truth.expected_fields:
        document_type, key = field.field.split(".", 1)
        if document_type != document.document_type:
            continue
        payload[key] = {
            "value": field.value,
            "citation": {
                "document_id": field.citation.document_id,
                "page": field.citation.page,
                "bbox": field.citation.bbox,
                "quote": field.value,
            },
            "confidence": 0.9,
        }
    return payload


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


def test_provider_extraction_accepts_valid_structured_output(generated_corpus) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = payload_from_truth(truth, document)
    provider = FakeProvider()
    provider.queue_structured(payload)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert extraction.error_code is None
    assert set(extraction.fields) == set(payload)
    assert extraction.fields["legal_name"].value == "Ridgeline Haulage LLC"
    assert extraction.fields["legal_name"].confidence == 0.9
    assert len(provider.calls) == 1
    assert provider.calls[0].schema_name == "ApplicationKycExtraction"
    for field in extraction.fields.values():
        verify_citation(path, field.citation, field.value)


def test_provider_schema_failure_retries_once_and_then_succeeds(generated_corpus) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = payload_from_truth(truth, document)
    provider = FakeProvider()
    provider.queue_structured({"legal_name": {"value": "incomplete schema"}})
    provider.queue_structured(payload)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert extraction.fields["ein"].value == "00-0000101"
    assert len(provider.calls) == 2


def test_two_schema_failures_become_needs_more_info(generated_corpus) -> None:
    _truth, _document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    provider = FakeProvider()
    provider.queue_structured({"legal_name": {"value": "incomplete schema"}})
    provider.queue_structured({"legal_name": {"value": "still incomplete"}})

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "needs_more_info"
    assert extraction.error_code == "schema_error"
    assert extraction.fields == {}
    assert extraction.error is not None
    assert len(provider.calls) == 2


def test_provider_citation_failure_is_retried_and_rejected(generated_corpus) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = payload_from_truth(truth, document)
    bad = deepcopy(payload)
    bad["legal_name"]["citation"]["bbox"] = [0.0, 0.0, 1.0, 1.0]
    provider = FakeProvider()
    provider.queue_structured(bad)
    provider.queue_structured(payload)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted"
    assert len(provider.calls) == 2


def test_two_citation_failures_become_needs_more_info(generated_corpus) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = payload_from_truth(truth, document)
    bad = deepcopy(payload)
    bad["legal_name"]["citation"]["quote"] = "A value that is not in the document"
    bad["legal_name"]["value"] = "A value that is not in the document"
    provider = FakeProvider()
    provider.queue_structured(bad)
    provider.queue_structured(bad)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "needs_more_info"
    assert extraction.error_code == "invalid_citation"
    assert len(provider.calls) == 2


def test_provider_transport_error_does_not_touch_the_schema_retry(generated_corpus) -> None:
    _truth, _document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )

    class OfflineProvider(FakeProvider):
        def complete_structured(self, messages, schema, **kwargs):  # type: ignore[no-untyped-def]
            raise ProviderTransportError("provider is offline")

    extraction = extract_with_provider(OfflineProvider(), path, classification)

    assert extraction.status == "needs_more_info"
    assert extraction.error_code == "provider_error"
    assert "offline" in (extraction.error or "")


def test_provider_extraction_rejects_a_citation_from_another_document(generated_corpus) -> None:
    truth, document, path, classification = corpus_document(
        generated_corpus, "case_onb_ridgeline", "application_kyc"
    )
    payload = payload_from_truth(truth, document)
    payload["legal_name"]["citation"]["document_id"] = "DOC-OTHER-APP"
    provider = FakeProvider()
    provider.queue_structured(payload)
    provider.queue_structured(payload)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "needs_more_info"
    assert extraction.error_code == "invalid_citation"
