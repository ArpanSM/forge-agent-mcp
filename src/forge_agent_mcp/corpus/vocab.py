"""Shared domain vocabulary for manifests and generated corpus models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

CaseType = Literal["onboarding", "invoice"]
Split = Literal["eval", "held_out"]
Category = Literal["clean", "hard_stop", "high", "medium", "low", "injection"]
Severity = Literal["hard_stop", "high", "medium", "low"]
Recommendation = Literal[
    "approve",
    "conditional_approve",
    "hold",
    "request_documents",
    "decline",
    "escalate",
]
Decision = Literal["approve", "conditional_approve", "decline", "request_documents"]
DecisionEffect = Literal[
    "decline_or_escalate",
    "hold_or_condition",
    "condition_or_reserve",
    "approve_with_clarification",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
