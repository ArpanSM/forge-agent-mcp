from __future__ import annotations

from forge_agent_mcp.extraction.extractor import extract_case
from forge_agent_mcp.rules.corpus import load_case_packet
from forge_agent_mcp.rules.engine import review
from tests.rules_helpers import replace_field_value, review_corpus_case


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


def test_missing_required_document_requests_documents(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_inv_sagebrush")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"missing_document"}
    flag = flags["missing_document"]
    assert flag.severity == "high"
    assert flag.evidence.missing_document_type == "proof_of_delivery"
    assert review.recommendation.recommendation == "request_documents"
    assert review.recommendation.decision == "request_documents"
    assert review.recommendation.conditions == ["Obtain a signed proof of delivery."]
    assert "The packet has no Proof of Delivery." in review.recommendation.evidence_gaps


def test_recent_bank_change_is_high_and_combines_with_missing_document(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_onb_blue_alder")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"bank_account_changed", "missing_document"}
    assert flags["bank_account_changed"].severity == "high"
    assert flags["bank_account_changed"].evidence.document_id == "DOC-ONB-BLUE-ALDER-BANK"
    assert review.recommendation.recommendation == "request_documents"
    assert review.recommendation.conditions == [
        "Obtain a current certificate of insurance.",
        "Verify the new bank account with a dated bank letter.",
    ]


def test_bank_account_name_mismatch_is_high(generated_corpus) -> None:
    _manifest, result = generated_corpus
    extraction = extract_case("case_onb_ridgeline", result.root)
    assert extraction.ok is True and extraction.data is not None
    changed = replace_field_value(
        extraction.data,
        "bank_letter",
        "account_holder",
        "Ridgeline Holdings LLC",
    )

    outcome = review(changed, load_case_packet("case_onb_ridgeline", result.root))

    flags = {flag.code: flag for flag in outcome.flags}
    assert set(flags) == {"bank_account_name_mismatch"}
    assert flags["bank_account_name_mismatch"].severity == "high"
    assert outcome.recommendation.recommendation == "conditional_approve"
    assert outcome.recommendation.conditions == ["Resolve the bank account-name mismatch."]


def test_duplicate_invoice_number_is_a_hard_stop(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_inv_cedar_pine")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"duplicate_invoice"}
    flag = flags["duplicate_invoice"]
    assert flag.severity == "hard_stop"
    assert flag.rule_id == "duplicate.invoice_number"
    assert flag.evidence.document_id == "DOC-INV-CEDAR-PINE-INV"
    assert "INV-CED-1002" in flag.details
    assert review.recommendation.recommendation == "decline"
    assert review.recommendation.decision == "decline"


def test_duplicate_invoice_document_hash_is_a_hard_stop(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_inv_harborline")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"duplicate_invoice"}
    flag = flags["duplicate_invoice"]
    assert flag.severity == "hard_stop"
    assert flag.rule_id == "duplicate.document_hash"
    assert flag.evidence.record_id == "REC-INV-HARBORLINE-PRIOR"
    assert flag.evidence.record_path == "invoices[0].document_hash"
    assert review.recommendation.recommendation == "decline"


def test_invoice_total_above_rate_confirmation_is_high(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_inv_northgate")

    flags = {flag.code: flag for flag in review.flags}
    assert "invoice_arithmetic_mismatch" in flags
    flag = flags["invoice_arithmetic_mismatch"]
    assert flag.severity == "high"
    assert flag.evidence.document_id == "DOC-INV-NORTHGATE-INV"
    assert "$2,950.00" in flag.details
    assert "$2,750.00" in flag.details


def test_pod_weight_mismatch_is_high_and_holds_with_arithmetic(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_inv_northgate")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"invoice_arithmetic_mismatch", "pod_mismatch"}
    flag = flags["pod_mismatch"]
    assert flag.severity == "high"
    assert flag.rule_id == "pod.weight"
    assert flag.evidence.document_id == "DOC-INV-NORTHGATE-POD"
    assert "17,500 lbs" in flag.details
    assert "16,000 lbs" in flag.details
    assert review.recommendation.recommendation == "hold"
    assert review.recommendation.decision == "request_documents"
    assert review.recommendation.conditions == [
        "Reconcile the invoice to the rate confirmation.",
        "Obtain a POD matching the BOL weight.",
    ]


def test_round_dollar_invoice_is_medium_and_conditional(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_inv_quarry_road")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"round_dollar_invoice"}
    assert flags["round_dollar_invoice"].severity == "medium"
    assert review.recommendation.recommendation == "conditional_approve"
    assert review.recommendation.conditions == ["Adjust reserve for the round-dollar invoice."]


def test_missing_optional_reference_is_low_and_approves_with_clarification(generated_corpus) -> None:
    review = review_corpus_case(generated_corpus, "case_inv_blue_alder")

    flags = {flag.code: flag for flag in review.flags}
    assert set(flags) == {"missing_optional_field"}
    flag = flags["missing_optional_field"]
    assert flag.severity == "low"
    assert flag.evidence.missing_field == "reference_number"
    assert review.recommendation.recommendation == "approve"
    assert review.recommendation.decision == "approve"
    assert review.recommendation.conditions == [
        "Confirm the optional invoice reference before funding."
    ]
