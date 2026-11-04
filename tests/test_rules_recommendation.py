"""Table-driven coverage for the fixed decision rubric."""

from __future__ import annotations

import pytest

from forge_agent_mcp.rules.catalog import make_flag
from forge_agent_mcp.rules.engine import recommend
from forge_agent_mcp.rules.models import Evidence, RuleFlag


def _flag(
    code: str,
    *,
    rule_id: str | None = None,
    document_type: str | None = None,
    missing_document_type: str | None = None,
) -> RuleFlag:
    defaults = {
        "missing_document": "documents.required",
        "document_instruction_injection": "injection.document_instruction",
        "watchlist_hit": "watchlist.match",
        "bank_account_changed": "bank.account_age",
        "invoice_arithmetic_mismatch": "invoice.arithmetic",
        "pod_mismatch": "pod.weight",
        "authority_young": "authority.age",
        "round_dollar_invoice": "invoice.round_dollar",
        "missing_optional_field": "invoice.optional_reference",
        "identity_mismatch": "identity.cross_document",
    }
    return make_flag(
        code,
        rule_id=rule_id or defaults.get(code, code),
        details=f"{code} detail",
        evidence=Evidence(
            document_id="DOC-TEST",
            document_type=document_type,
            missing_document_type=missing_document_type,
        ),
    )


def _high(rule_id: str = "bank.account_age") -> RuleFlag:
    if rule_id == "invoice.arithmetic":
        return _flag("invoice_arithmetic_mismatch")
    return _flag("bank_account_changed")


RUBRIC = [
    pytest.param([], "approve", "approve", [], id="clean"),
    pytest.param(
        [_flag("missing_optional_field")],
        "approve",
        "approve",
        ["Confirm the optional invoice reference before funding."],
        id="low-clarification",
    ),
    pytest.param(
        [_flag("authority_young")],
        "conditional_approve",
        "conditional_approve",
        ["Obtain six months of authority history or adjust reserve."],
        id="medium-condition",
    ),
    pytest.param(
        [_high()],
        "conditional_approve",
        "conditional_approve",
        ["Verify the new bank account with a dated bank letter."],
        id="one-high",
    ),
    pytest.param(
        [_high(), _high("invoice.arithmetic")],
        "hold",
        "request_documents",
        [
            "Verify the new bank account with a dated bank letter.",
            "Reconcile the invoice to the rate confirmation.",
        ],
        id="two-high-hold",
    ),
    pytest.param(
        [
            _flag(
                "missing_document",
                missing_document_type="proof_of_delivery",
                document_type="proof_of_delivery",
            ),
            _high(),
        ],
        "request_documents",
        "request_documents",
        [
            "Obtain a signed proof of delivery.",
            "Verify the new bank account with a dated bank letter.",
        ],
        id="missing-document-request",
    ),
    pytest.param(
        [_flag("document_instruction_injection", document_type="application_kyc")],
        "hold",
        "request_documents",
        ["Quarantine the injected application text and review the packet manually."],
        id="injection-hold",
    ),
    pytest.param(
        [
            _flag("document_instruction_injection", document_type="proof_of_delivery"),
            _high(),
        ],
        "hold",
        "request_documents",
        [
            "Quarantine the injected POD text and review the packet manually.",
            "Verify the new bank account with a dated bank letter.",
        ],
        id="injection-with-high",
    ),
    pytest.param(
        [_flag("identity_mismatch")],
        "decline",
        "decline",
        [],
        id="hard-stop-decline",
    ),
    pytest.param(
        [_flag("watchlist_hit")],
        "escalate",
        "decline",
        [],
        id="watchlist-escalate",
    ),
    pytest.param(
        [
            _flag("identity_mismatch"),
            _flag("document_instruction_injection", document_type="application_kyc"),
            _high(),
        ],
        "decline",
        "decline",
        [],
        id="hard-stop-beats-injection-and-high",
    ),
]


@pytest.mark.parametrize(
    ("flags", "recommendation", "decision", "conditions"),
    RUBRIC,
)
def test_decision_rubric(
    flags: list[RuleFlag],
    recommendation: str,
    decision: str,
    conditions: list[str],
) -> None:
    outcome = recommend(flags)

    assert outcome.recommendation == recommendation
    assert outcome.decision == decision
    assert outcome.conditions == conditions
    assert outcome.rationale


def test_evidence_gaps_are_preserved_without_changing_a_clean_decision() -> None:
    outcome = recommend([], evidence_gaps=["The packet has no Proof of Delivery."])

    assert outcome.recommendation == "approve"
    assert outcome.evidence_gaps == ["The packet has no Proof of Delivery."]
