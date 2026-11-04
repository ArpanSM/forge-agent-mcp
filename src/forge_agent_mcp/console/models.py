"""Typed view models for the reviewer console."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..agent.models import CaseResult, ExtractedFieldView
from ..corpus.vocab import CaseType, Decision, Recommendation
from ..gateway.models import AuditEvent, CaseView

QueueStatus = Literal[
    "pending_review",
    "extracting",
    "pending_approval",
    "awaiting_documents",
    "approved",
    "declined",
    "unavailable",
]

STATUS_LABELS: dict[str, str] = {
    "pending_review": "Pending review",
    "extracting": "In progress",
    "pending_approval": "Pending approval",
    "awaiting_documents": "Awaiting documents",
    "approved": "Approved",
    "declined": "Declined",
    "unavailable": "Unavailable",
}

DECISION_LABELS: dict[str, str] = {
    "approve": "Approve",
    "conditional_approve": "Conditional approve",
    "decline": "Decline",
    "request_documents": "Request documents",
}

DECISION_STATUS_LABELS: dict[str, str] = {
    "approve": "Approved",
    "conditional_approve": "Conditionally approved",
    "decline": "Declined",
    "request_documents": "Awaiting documents",
}


@dataclass(frozen=True)
class CaseCatalogEntry:
    case_id: str
    case_type: CaseType
    carrier_name: str


@dataclass
class CaseQueueItem:
    case_id: str
    case_type: CaseType
    carrier_name: str
    status: str
    status_label: str
    recommendation: Recommendation | None = None
    decision: Decision | None = None
    flags: dict[str, int] = field(default_factory=dict)
    pending_approval: bool = False

    @property
    def flag_count(self) -> int:
        return sum(self.flags.values())


@dataclass
class CaseDetail:
    view: CaseView
    result: CaseResult | None
    audit: list[AuditEvent]
    fields: list[ExtractedFieldView]
    allowed_actions: list[Decision]
    blocker: str | None = None

    @property
    def pending_approval(self) -> bool:
        return (
            self.result is not None
            and self.result.status == "pending_approval"
            and self.view.decision is None
        )

    @property
    def decision(self) -> Decision | None:
        if self.result and self.result.decision:
            return self.result.decision.decision
        if self.view.decision:
            return self.view.decision.decision
        return None

    @property
    def recommended_action(self) -> Decision | None:
        if self.result and self.result.recommendation:
            return self.result.recommendation.decision
        return None


__all__ = [
    "CaseCatalogEntry",
    "CaseDetail",
    "CaseQueueItem",
    "DECISION_LABELS",
    "DECISION_STATUS_LABELS",
    "STATUS_LABELS",
]
