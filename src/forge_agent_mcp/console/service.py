"""Console orchestration: queue, evidence, approval token minting and resume."""

from __future__ import annotations

from collections.abc import Sequence

from ..agent.gateway import GatewayClient, GatewayToolError
from ..agent.graph import CaseRunError, CaseRunner
from ..agent.models import (
    ApprovalAction,
    CaseResult,
)
from ..corpus.manifest import Manifest
from ..corpus.models import CorpusIndex
from ..corpus.vocab import Decision, Recommendation
from ..gateway.approval import ApprovalTokenIssuer
from ..gateway.auth import APPROVE_SCOPE, READ_SCOPE, LocalTokenIssuer
from ..gateway.models import AuditEvent, CaseView, FlagRecord
from .models import (
    DECISION_LABELS,
    DECISION_STATUS_LABELS,
    STATUS_LABELS,
    CaseCatalogEntry,
    CaseDetail,
    CaseQueueItem,
)

HARD_STOP_CODES = frozenset(
    {
        "watchlist_hit",
        "authority_inactive",
        "identity_mismatch",
        "insurance_expired",
        "insurance_below_minimum",
        "duplicate_invoice",
    }
)
UNTRUSTED_CODES = frozenset({"document_instruction_injection"})
MISSING_DOCUMENT_CODES = frozenset({"missing_document"})
ALL_ACTIONS: list[Decision] = [
    "approve",
    "conditional_approve",
    "decline",
    "request_documents",
]
HOLD_ACTIONS: list[Decision] = ["decline", "request_documents"]
HARD_STOP_ACTIONS: list[Decision] = ["decline"]


class ConsoleError(Exception):
    """A console action that cannot be completed, with an HTTP status."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def recommendation_label(value: Recommendation | None) -> str:
    if value is None:
        return "No recommendation"
    return value.replace("_", " ").capitalize()


def decision_label(value: Decision | None) -> str:
    if value is None:
        return ""
    return DECISION_LABELS[value]


def decision_status_label(value: Decision | None) -> str:
    if value is None:
        return ""
    return DECISION_STATUS_LABELS[value]


def blocked_reason(flags: Sequence[FlagRecord] | Sequence[object]) -> str | None:
    codes = {getattr(flag, "code", "") for flag in flags}
    if codes & HARD_STOP_CODES:
        return (
            "A hard-stop flag blocks approval and document requests. Decline the case; "
            "a hard stop can never be approved."
        )
    if codes & UNTRUSTED_CODES:
        return (
            "Document-embedded instructions were found and quarantined. Approval is "
            "blocked; request documents or decline after a manual review."
        )
    if codes & MISSING_DOCUMENT_CODES:
        return (
            "Required packet documents are missing. Request documents or decline; "
            "funding cannot be approved from an incomplete packet."
        )
    return None


def allowed_actions(flags: Sequence[FlagRecord] | Sequence[object]) -> list[Decision]:
    """Return the decisions the console will accept for this evidence."""

    codes = {getattr(flag, "code", "") for flag in flags}
    if codes & HARD_STOP_CODES:
        return list(HARD_STOP_ACTIONS)
    if codes & (UNTRUSTED_CODES | MISSING_DOCUMENT_CODES):
        return list(HOLD_ACTIONS)
    return list(ALL_ACTIONS)


class ConsoleService:
    """The small application layer shared by the routes and the demo."""

    def __init__(
        self,
        *,
        runner: CaseRunner,
        gateway: GatewayClient,
        catalog: Sequence[CaseCatalogEntry],
        approval_issuer: ApprovalTokenIssuer,
        token_issuer: LocalTokenIssuer,
        tenant_id: str,
        default_approver: str = "underwriter@example.test",
    ) -> None:
        self.runner = runner
        self.gateway = gateway
        self.catalog = list(catalog)
        self.approval_issuer = approval_issuer
        self.token_issuer = token_issuer
        self.tenant_id = tenant_id
        self.default_approver = default_approver

    # -- reads -----------------------------------------------------------

    def queue(self) -> list[CaseQueueItem]:
        items: list[CaseQueueItem] = []
        for entry in self.catalog:
            result = self.runner.get(entry.case_id)
            try:
                view = self.gateway.read_case(entry.case_id)
            except GatewayToolError:
                items.append(
                    CaseQueueItem(
                        case_id=entry.case_id,
                        case_type=entry.case_type,
                        carrier_name=entry.carrier_name,
                        status="unavailable",
                        status_label=STATUS_LABELS["unavailable"],
                        flags={"hard_stop": 0, "high": 0, "medium": 0, "low": 0},
                    )
                )
                continue
            status = self._status(view, result)
            flags = result.flags if result is not None else view.flags
            counts = {"hard_stop": 0, "high": 0, "medium": 0, "low": 0}
            for flag in flags:
                counts[flag.severity] = counts.get(flag.severity, 0) + 1
            items.append(
                CaseQueueItem(
                    case_id=entry.case_id,
                    case_type=entry.case_type,
                    carrier_name=view.carrier.legal_name,
                    status=status,
                    status_label=STATUS_LABELS.get(status, status.replace("_", " ").title()),
                    recommendation=(
                        result.recommendation.recommendation
                        if result is not None and result.recommendation is not None
                        else (
                            view.recommendation.recommendation
                            if view.recommendation is not None
                            else None
                        )
                    ),
                    decision=(
                        result.decision.decision
                        if result and result.decision
                        else (view.decision.decision if view.decision else None)
                    ),
                    flags=counts,
                    pending_approval=(
                        result is not None
                        and result.status == "pending_approval"
                        and view.decision is None
                    ),
                )
            )
        return items

    def _status(self, view: CaseView, result: CaseResult | None) -> str:
        if view.decision is not None:
            return {
                "approve": "approved",
                "conditional_approve": "approved",
                "decline": "declined",
                "request_documents": "awaiting_documents",
            }[view.decision.decision]
        if result is not None:
            if result.status == "pending_approval":
                return "pending_approval"
            if result.decision is not None:
                return {
                    "approve": "approved",
                    "conditional_approve": "approved",
                    "decline": "declined",
                    "request_documents": "awaiting_documents",
                }[result.decision.decision]
        return view.status

    def detail(self, case_id: str) -> CaseDetail:
        try:
            view = self.gateway.read_case(case_id)
        except GatewayToolError as exc:
            if exc.code == "case_not_found":
                raise ConsoleError(
                    "case_not_found",
                    f"Case {case_id!r} was not found.",
                    status_code=404,
                ) from exc
            raise ConsoleError(exc.code, exc.message, status_code=502) from exc
        result = self.runner.get(case_id)
        try:
            audit = self.gateway.get_audit(case_id, limit=200).events
        except GatewayToolError:
            audit = list(result.audit) if result is not None else []
        flags = result.flags if result is not None else view.flags
        blocker = blocked_reason(flags)
        return CaseDetail(
            view=view,
            result=result,
            audit=list(audit),
            fields=result.fields() if result is not None else [],
            allowed_actions=allowed_actions(flags),
            blocker=blocker,
        )

    def audit(self, case_id: str) -> list[AuditEvent]:
        detail = self.detail(case_id)
        return detail.audit

    # -- actions ---------------------------------------------------------

    def run(self, case_id: str) -> CaseResult:
        try:
            view = self.gateway.read_case(case_id)
        except GatewayToolError as exc:
            raise ConsoleError(
                "case_not_found",
                f"Case {case_id!r} was not found.",
                status_code=404,
            ) from exc
        if view.decision is not None:
            raise ConsoleError(
                "case_already_decided",
                f"Case {case_id!r} already has a decision and cannot be run again.",
                status_code=409,
            )
        existing = self.runner.get(case_id)
        if existing is not None and existing.decision is not None:
            raise ConsoleError(
                "case_already_decided",
                f"Case {case_id!r} already has a decision and cannot be run again.",
                status_code=409,
            )
        if existing is not None and existing.status in {"pending_approval", "in_progress"}:
            return existing
        result = self.runner.start(case_id)
        if result.status == "failed":
            raise ConsoleError(
                "case_run_failed",
                result.error or f"Case {case_id!r} could not be run.",
                status_code=502,
            )
        return result

    def decide(
        self,
        case_id: str,
        *,
        action: str,
        approver: str,
        note: str = "",
        conditions: Sequence[str] | None = None,
    ) -> CaseResult:
        if action not in ALL_ACTIONS:
            raise ConsoleError(
                "invalid_action",
                f"Unknown review action {action!r}.",
                status_code=400,
            )
        decision: Decision = action  # type: ignore[assignment]
        reviewer = approver.strip()
        if not reviewer:
            raise ConsoleError(
                "approver_required",
                "A reviewer identity is required before a decision can be recorded.",
                status_code=400,
            )
        current = self.runner.get(case_id)
        if current is None:
            raise ConsoleError(
                "case_not_started",
                f"Case {case_id!r} has not been run. Start it before deciding.",
                status_code=409,
            )
        if current.decision is not None:
            raise ConsoleError(
                "case_already_decided",
                f"Case {case_id!r} already has a decision; it cannot be decided again.",
                status_code=409,
            )
        if current.status != "pending_approval":
            raise ConsoleError(
                "case_not_pending",
                f"Case {case_id!r} is not waiting for a human decision.",
                status_code=409,
            )
        try:
            live = self.gateway.read_case(case_id)
        except GatewayToolError as exc:
            raise ConsoleError(exc.code, exc.message, status_code=502) from exc
        if live.decision is not None:
            raise ConsoleError(
                "case_already_decided",
                f"Case {case_id!r} already has a committed decision; it cannot be written again.",
                status_code=409,
            )
        blocker = blocked_reason(current.flags)
        allowed = allowed_actions(current.flags)
        if decision not in allowed:
            raise ConsoleError(
                "decision_blocked",
                blocker or f"The {decision} action is not available for this case.",
                status_code=409,
            )

        recommendation = current.recommendation
        if recommendation is None:
            raise ConsoleError(
                "recommendation_missing",
                f"Case {case_id!r} has no recommendation to act on.",
                status_code=409,
            )
        cleaned_conditions = [item.strip() for item in (conditions or []) if item and item.strip()]
        if decision == "conditional_approve":
            if not cleaned_conditions:
                raise ConsoleError(
                    "conditions_required",
                    "A conditional approval requires at least one condition.",
                    status_code=409,
                )
        elif decision in {"approve", "decline"}:
            cleaned_conditions = []
        elif not cleaned_conditions:
            cleaned_conditions = list(recommendation.conditions)

        rationale = self._rationale(
            reviewer=reviewer,
            decision=decision,
            recommendation=recommendation.recommendation,
            recommendation_rationale=recommendation.rationale,
            note=note,
        )
        approval_token = self.approval_issuer.issue_for_decision(
            approver=reviewer,
            tenant_id=self.tenant_id,
            case_id=case_id,
            decision=decision,
            rationale=rationale,
            conditions=cleaned_conditions,
            ttl_seconds=self.approval_issuer.default_ttl_seconds,
        )
        approval_jwt = self.token_issuer.issue(
            subject=reviewer,
            tenant_id=self.tenant_id,
            scopes=[READ_SCOPE, APPROVE_SCOPE],
            ttl_seconds=300,
        )
        action_payload = ApprovalAction(
            action=decision,
            approver=reviewer,
            rationale=rationale,
            conditions=cleaned_conditions,
            note=note.strip(),
            approval_token=approval_token,
            approval_jwt=approval_jwt,
        )
        try:
            result = self.runner.resume(case_id, action_payload)
        except CaseRunError as exc:
            raise ConsoleError("case_not_pending", str(exc), status_code=409) from exc
        if result.status == "failed":
            raise ConsoleError(
                "decision_failed",
                result.error or "The decision could not be committed.",
                status_code=502,
            )
        return result

    def _rationale(
        self,
        *,
        reviewer: str,
        decision: Decision,
        recommendation: str,
        recommendation_rationale: str,
        note: str,
    ) -> str:
        label = DECISION_LABELS[decision].lower()
        rationale = (
            f"{reviewer} chose to {label}. Agent recommendation: {recommendation}. "
            f"{recommendation_rationale}"
        )
        if note.strip():
            rationale += f" Reviewer note: {note.strip()}"
        return rationale


def catalog_from_corpus(manifest: Manifest, index: CorpusIndex) -> list[CaseCatalogEntry]:
    """Build the console queue from the generated corpus index."""

    carriers = {carrier.carrier_id: carrier for carrier in manifest.carriers}
    entries: list[CaseCatalogEntry] = []
    for case in index.cases:
        carrier = carriers[case.carrier_id]
        entries.append(
            CaseCatalogEntry(
                case_id=case.case_id,
                case_type=case.case_type,
                carrier_name=carrier.legal_name,
            )
        )
    return entries


__all__ = [
    "ALL_ACTIONS",
    "HARD_STOP_ACTIONS",
    "HOLD_ACTIONS",
    "ConsoleError",
    "ConsoleService",
    "allowed_actions",
    "blocked_reason",
    "catalog_from_corpus",
    "decision_label",
    "decision_status_label",
    "recommendation_label",
]
