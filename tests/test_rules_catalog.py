"""Guard the static rule catalogue against drift from the corpus manifest."""

from __future__ import annotations

from forge_agent_mcp.corpus.manifest import load_manifest
from forge_agent_mcp.rules.catalog import DOCUMENT_TITLES, FLAG_CATALOG

MANIFEST_PATH = "corpus/manifest.json"


def test_flag_catalogue_matches_the_manifest() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    assert [flag.code for flag in FLAG_CATALOG] == [flag.code for flag in manifest.flag_catalog]
    for ours, theirs in zip(FLAG_CATALOG, manifest.flag_catalog, strict=True):
        assert ours.severity == theirs.severity
        assert ours.rule == theirs.rule
        assert ours.decision_effect == theirs.decision_effect
        assert ours.default_rule_id


def test_document_titles_match_the_manifest() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    assert {
        code: definition.title for code, definition in manifest.document_types.items()
    } == DOCUMENT_TITLES
