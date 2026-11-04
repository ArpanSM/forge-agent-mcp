from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from forge_agent_mcp.corpus.generator import GenerationResult, generate_corpus
from forge_agent_mcp.corpus.manifest import Manifest, load_manifest

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "corpus" / "manifest.json"


@pytest.fixture(scope="session")
def generated_corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[Manifest, GenerationResult]:
    manifest = load_manifest(MANIFEST_PATH)
    root = tmp_path_factory.mktemp("generated-corpus") / "out"
    return manifest, generate_corpus(manifest, root)


@pytest.fixture
def provider_payload():
    def build(truth, document) -> dict[str, Any]:
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

    return build
