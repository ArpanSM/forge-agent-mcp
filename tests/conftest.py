from __future__ import annotations

from pathlib import Path

import pytest

from forge_agent_mcp.corpus.generator import GenerationResult, generate_corpus
from forge_agent_mcp.corpus.manifest import Manifest, load_manifest

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "corpus" / "manifest.json"


@pytest.fixture(scope="session")
def generated_corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[Manifest, GenerationResult]:
    manifest = load_manifest(MANIFEST_PATH)
    root = tmp_path_factory.mktemp("generated-corpus") / "out"
    return manifest, generate_corpus(manifest, root)
