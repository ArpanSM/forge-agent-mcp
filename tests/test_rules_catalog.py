"""Guard the static rule catalogue against drift from the corpus manifest."""

from __future__ import annotations

from pathlib import Path

from forge_agent_mcp.corpus.manifest import load_manifest
from forge_agent_mcp.rules.catalog import (
    DOCUMENT_TITLES,
    FLAG_BY_CODE,
    FLAG_CATALOG,
    FUTURE_FLAG_CODES,
)

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "corpus" / "manifest.json"


def test_flag_catalogue_matches_the_manifest() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    manifest_flags = {flag.code: flag for flag in manifest.flag_catalog}
    active_codes = [code for code in manifest_flags if code not in FUTURE_FLAG_CODES]

    assert [flag.code for flag in FLAG_CATALOG] == active_codes
    for ours in FLAG_CATALOG:
        theirs = manifest_flags[ours.code]
        assert ours.severity == theirs.severity
        assert ours.rule == theirs.rule
        assert ours.decision_effect == theirs.decision_effect


def test_deferred_flags_are_documented_and_not_emitted() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    manifest_codes = {flag.code for flag in manifest.flag_catalog}

    assert {"altered_metadata", "formatting_inconsistency"} == FUTURE_FLAG_CODES
    assert manifest_codes >= FUTURE_FLAG_CODES
    assert not FUTURE_FLAG_CODES & set(FLAG_BY_CODE)


def test_document_titles_match_the_manifest() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    assert {
        code: definition.title for code, definition in manifest.document_types.items()
    } == DOCUMENT_TITLES
