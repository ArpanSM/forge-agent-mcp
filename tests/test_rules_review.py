from __future__ import annotations

import pytest

from forge_agent_mcp.extraction.extractor import extract_case
from forge_agent_mcp.rules.corpus import RuleEngineError, load_case_packet
from forge_agent_mcp.rules.engine import review_case, validate
from tests.rules_helpers import review_corpus_case


def test_clean_onboarding_case_reviews_with_no_flags_and_approve(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_onb_ridgeline")

    assert review.case_id == "case_onb_ridgeline"
    assert review.case_type == "onboarding"
    assert review.flags == []
    assert review.recommendation.recommendation == "approve"
    assert review.recommendation.decision == "approve"
    assert review.recommendation.conditions == []
    assert review.recommendation.evidence_gaps == []
    assert "approve" in review.summary.lower()
    assert review.summary.count("No rule flags") == 1


def test_review_case_returns_an_envelope_for_an_unknown_case(generated_corpus) -> None:
    _manifest, result = generated_corpus

    outcome = review_case("case_does_not_exist", result.root)

    assert outcome.ok is False
    assert outcome.error_code == "unknown_case"
    assert "unknown case" in (outcome.message or "")
    assert outcome.data is None


def test_review_case_returns_an_envelope_for_a_missing_corpus(tmp_path) -> None:
    outcome = review_case("case_onb_ridgeline", tmp_path / "absent")

    assert outcome.ok is False
    assert outcome.error_code == "corpus_not_found"
    assert "corpus" in (outcome.message or "")
    assert outcome.data is None


def test_validate_rejects_an_extraction_from_another_case(generated_corpus) -> None:
    _manifest, result = generated_corpus
    extraction = extract_case("case_onb_ridgeline", result.root)
    assert extraction.ok is True and extraction.data is not None
    packet = load_case_packet("case_onb_meridian", result.root)

    with pytest.raises(ValueError, match="does not match"):
        validate(extraction.data, packet)


def test_rule_engine_error_is_a_domain_exception() -> None:
    error = RuleEngineError("corpus_invalid", "bad record")

    assert error.code == "corpus_invalid"
    assert "bad record" in str(error)
