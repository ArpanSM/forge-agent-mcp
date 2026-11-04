"""The demo underwriting policy pack used by the pure rule engine.

These thresholds are synthetic and mirror the policy exposed by the gateway and the
six-month / 30-day rules in ``CONTEXT.md``. They are not legal or production policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RulePolicy:
    policy_id: str
    min_auto_liability: Decimal
    min_cargo_limit: Decimal
    authority_min_age_days: int
    bank_change_window_days: int
    round_dollar_multiple: Decimal
    high_flag_hold_threshold: int


DEMO_POLICY = RulePolicy(
    policy_id="policy_demo_v1",
    min_auto_liability=Decimal("1000000.00"),
    min_cargo_limit=Decimal("100000.00"),
    authority_min_age_days=183,
    bank_change_window_days=30,
    round_dollar_multiple=Decimal("500.00"),
    high_flag_hold_threshold=2,
)


__all__ = ["DEMO_POLICY", "RulePolicy"]
