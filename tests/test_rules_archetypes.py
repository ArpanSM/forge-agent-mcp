"""Table-driven acceptance coverage for every corpus archetype.

Each case is extracted from its PDFs, then reviewed. The generated ground truth is
used only as an oracle for the assertion; it is never passed into the rule engine.
"""

from __future__ import annotations

import pytest

from forge_agent_mcp.corpus.manifest import load_manifest
from forge_agent_mcp.extraction.extractor import extract_case
from forge_agent_mcp.rules.corpus import load_case_packet
from forge_agent_mcp.rules.engine import review

MANIFEST_PATH = "corpus/manifest.json"

CASES = [case.case_id for case in load_manifest(MANIFEST_PATH).cases]


@pytest.mark.parametrize("case_id", CASES)
def test_corpus_case_matches_its_underwriting_labels(generated_corpus, case_id: str) -> None:
    manifest, generated = generated_corpus
    expected = generated.cases[case_id]
    expected_codes = [flag.code for flag in expected.expected_flags]
    severities = {flag.code: flag.severity for flag in manifest.flag_catalog}

    extraction = extract_case(case_id, generated.root)
    assert extraction.ok is True, extraction.message
    assert extraction.data is not None
    packet = load_case_packet(case_id, generated.root)

    outcome = review(extraction.data, packet)

    assert [flag.code for flag in outcome.flags] == expected_codes
    assert [flag.severity for flag in outcome.flags] == [
        severities[code] for code in expected_codes
    ]
    assert outcome.recommendation.recommendation == expected.expected_recommendation
    assert outcome.recommendation.decision == expected.expected_decision
    assert outcome.recommendation.conditions == expected.expected_conditions
    assert outcome.case_id == expected.case_id
    assert outcome.case_type == expected.case_type
    assert outcome.as_of is not None


@pytest.mark.parametrize("case_id", CASES)
def test_every_flag_has_a_rule_id_and_a_citation(generated_corpus, case_id: str) -> None:
    _manifest, generated = generated_corpus
    extraction = extract_case(case_id, generated.root)
    assert extraction.ok is True and extraction.data is not None

    outcome = review(extraction.data, load_case_packet(case_id, generated.root))

    for flag in outcome.flags:
        assert flag.rule_id
        assert flag.rule
        evidence = flag.evidence
        located = any(
            (
                evidence.document_id,
                evidence.record_id,
                evidence.missing_document_type,
                evidence.missing_field,
            )
        )
        assert located, f"{flag.code} has no evidence location"


def test_all_required_archetypes_are_covered_by_the_table() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    all_archetypes = {archetype for case in manifest.cases for archetype in case.archetypes}

    assert set(manifest.required_eval_archetypes) <= all_archetypes
    assert {
        "clean",
        "expired_insurance",
        "identity_mismatch",
        "watchlist_hit",
        "inactive_authority",
        "duplicate_invoice",
        "duplicate_invoice_hash",
        "invoice_arithmetic_pod_mismatch",
        "missing_document",
        "prompt_injection",
        "bank_change",
        "authority_young",
        "round_dollar_invoice",
        "missing_optional_field",
    } <= all_archetypes
