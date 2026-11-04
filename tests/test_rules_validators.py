from __future__ import annotations

from tests.rules_helpers import review_corpus_case


def test_identity_mismatch_across_w9_is_a_hard_stop(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_onb_harborline")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"identity_mismatch"}

    flag = flags["identity_mismatch"]
    assert flag.severity == "hard_stop"
    assert flag.rule_id
    assert flag.evidence.document_id == "DOC-ONB-HARBORLINE-W9"
    assert review.recommendation.recommendation == "decline"
    assert review.recommendation.decision == "decline"


def test_expired_and_below_minimum_insurance_are_hard_stops(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_onb_cedar_pine")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"insurance_expired", "insurance_below_minimum"}
    assert all(flag.severity == "hard_stop" for flag in flags.values())
    assert flags["insurance_expired"].evidence.document_id == "DOC-ONB-CEDAR-PINE-COI"
    assert "2025-08-01" in flags["insurance_expired"].details
    assert "2026-11-01" in flags["insurance_expired"].details
    assert review.recommendation.recommendation == "decline"
    assert review.recommendation.decision == "decline"


def test_inactive_authority_is_a_hard_stop(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_onb_sagebrush")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"authority_inactive"}
    assert flags["authority_inactive"].severity == "hard_stop"
    assert flags["authority_inactive"].evidence.document_id == "DOC-ONB-SAGEBRUSH-MC"
    assert review.recommendation.recommendation == "decline"


def test_authority_younger_than_six_months_is_a_medium_condition(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_onb_quarry_road")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"authority_young"}
    assert flags["authority_young"].severity == "medium"
    assert flags["authority_young"].evidence.document_id == "DOC-ONB-QUARRY-ROAD-MC"
    assert review.recommendation.recommendation == "conditional_approve"
    assert review.recommendation.decision == "conditional_approve"
    assert review.recommendation.conditions == [
        "Obtain six months of authority history or adjust reserve."
    ]


def test_watchlist_hit_is_a_hard_stop_that_escalates(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_onb_northgate")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"watchlist_hit"}
    flag = flags["watchlist_hit"]
    assert flag.severity == "hard_stop"
    assert flag.evidence.record_id == "REC-ONB-NORTHGATE-WATCH"
    assert flag.evidence.record_path == "matches[0]"
    assert review.recommendation.recommendation == "escalate"
    assert review.recommendation.decision == "decline"
