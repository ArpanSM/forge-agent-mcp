"""The demo policy pack must stay aligned with the gateway's exposed thresholds."""

from __future__ import annotations

from forge_agent_mcp.gateway.models import PolicyThresholds
from forge_agent_mcp.rules.policy import DEMO_POLICY


def test_demo_policy_matches_the_gateway_policy_thresholds() -> None:
    gateway = PolicyThresholds()

    assert int(DEMO_POLICY.min_auto_liability) == gateway.min_auto_liability
    assert int(DEMO_POLICY.min_cargo_limit) == gateway.min_cargo_limit
    assert DEMO_POLICY.authority_min_age_days == gateway.min_authority_age_days
    assert DEMO_POLICY.bank_change_window_days == gateway.bank_change_window_days
    assert DEMO_POLICY.high_flag_hold_threshold == gateway.high_flag_hold_threshold
