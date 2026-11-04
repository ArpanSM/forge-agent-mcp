from __future__ import annotations

import os

import pytest

from forge_agent_mcp.config import Settings
from forge_agent_mcp.extraction.citations import verify_citation
from forge_agent_mcp.extraction.classify import classify_document
from forge_agent_mcp.extraction.provider import extract_with_provider
from forge_agent_mcp.providers import build_provider

LIVE_ENABLED = os.environ.get("FORGE_LIVE_MODEL") == "1"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not LIVE_ENABLED,
        reason="live model not enabled; set FORGE_LIVE_MODEL=1 and configure a provider",
    ),
]


def test_live_provider_extracts_a_w9_with_verified_citations(generated_corpus) -> None:
    _manifest, result = generated_corpus
    case_id = "case_onb_ridgeline"
    truth = result.cases[case_id]
    document = next(item for item in truth.documents if item.document_type == "w9")
    path = result.root / "cases" / case_id / document.filename
    classification = classify_document(
        path,
        document_id=document.document_id,
        title=document.title,
        filename=document.filename,
    )
    if os.environ.get("ORCAROUTER_API_KEY"):
        settings = Settings(model_provider="orcarouter", _env_file=None)
    else:
        settings = Settings(model_provider="lfm3", _env_file=None)
    provider = build_provider(settings)

    extraction = extract_with_provider(provider, path, classification)

    assert extraction.status == "extracted", extraction.error
    assert extraction.fields["legal_name"].value
    assert extraction.fields["ein"].value
    for field in extraction.fields.values():
        verify_citation(path, field.citation, field.value)
