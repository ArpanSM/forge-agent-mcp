"""Case-type-specific policy packs.

The manifest's flag catalog is global because the rule engine needs every code.
The gateway exposes only the flags that apply to the requested case type, so an
onboarding reviewer never sees invoice-only signals (and vice versa).
"""

from __future__ import annotations

from ..corpus.manifest import (
    INVOICE_DOCUMENTS,
    ONBOARDING_DOCUMENTS,
    REQUIRED_RECORDS,
    Manifest,
)
from ..corpus.vocab import CaseType
from .models import PolicyDecisionRule, PolicyFlag, PolicyPack, PolicyThresholds

INVOICE_ONLY_FLAGS = frozenset(
    {
        "duplicate_invoice",
        "invoice_arithmetic_mismatch",
        "pod_mismatch",
        "round_dollar_invoice",
    }
)
ONBOARDING_ONLY_FLAGS = frozenset(
    {
        "watchlist_hit",
        "authority_inactive",
        "identity_mismatch",
        "insurance_expired",
        "insurance_below_minimum",
        "bank_account_changed",
        "bank_account_name_mismatch",
        "authority_young",
    }
)


def build_policy_pack(manifest: Manifest, case_type: CaseType) -> PolicyPack:
    """Build the deterministic policy pack exposed by ``get_policy``."""

    required_documents = ONBOARDING_DOCUMENTS if case_type == "onboarding" else INVOICE_DOCUMENTS
    excluded = INVOICE_ONLY_FLAGS if case_type == "onboarding" else ONBOARDING_ONLY_FLAGS
    return PolicyPack(
        policy_id=f"policy_{case_type}_v1",
        case_type=case_type,
        as_of=manifest.as_of.isoformat(),
        thresholds=PolicyThresholds(),
        required_documents=sorted(required_documents),
        required_records=sorted(REQUIRED_RECORDS[case_type]),
        flags=[
            PolicyFlag(
                code=flag.code,
                severity=flag.severity,
                rule=flag.rule,
                decision_effect=flag.decision_effect,
            )
            for flag in manifest.flag_catalog
            if flag.code not in excluded
        ],
        decision_rule=PolicyDecisionRule(
            hard_stop="Decline or escalate; never approve.",
            high="Hold, request documents or attach a condition.",
            medium="Conditional approval or adjusted reserve.",
            low="Approve with clarification.",
            clean="Approve.",
        ),
    )
