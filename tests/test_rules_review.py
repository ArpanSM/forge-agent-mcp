from __future__ import annotations

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
