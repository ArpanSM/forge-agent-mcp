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
