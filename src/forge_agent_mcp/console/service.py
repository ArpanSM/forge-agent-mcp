"""Console orchestration: queue, evidence, approval token minting and resume."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from ..agent.gateway import GatewayClient, GatewayToolError
from ..agent.graph import CaseRunError, CaseRunner
from ..agent.models import ApprovalAction, CaseResult
from ..corpus.manifest import Manifest
from ..corpus.models import CorpusIndex
from ..corpus.vocab import Decision, Recommendation, Severity
from ..gateway.approval import ApprovalTokenIssuer
from ..gateway.auth import APPROVE_SCOPE, READ_SCOPE, LocalTokenIssuer
from ..gateway.models import CaseView, FlagRecord
from ..gateway.store import decision_outcome
from .models import (
    DECISION_LABELS,
    DECISION_STATUS_LABELS,
    STATUS_LABELS,
    CaseCatalogEntry,
    CaseDetail,
    CaseQueueItem,
    QueueStatus,
)

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


def decision_status(value: Decision) -> QueueStatus:
    return cast(QueueStatus, decision_outcome(value).case_status)


def _has_hard_stop(flags: Sequence[FlagRecord] | Sequence[object]) -> bool:
    return any(getattr(flag, "severity", None) == "hard_stop" for flag in flags)


def allowed_actions(
    result: CaseResult | None,
    flags: Sequence[FlagRecord] | Sequence[object],
) -> list[Decision]:
    """Derive the review actions from severity and the recommendation."""

    if _has_hard_stop(flags):
        return list(HARD_STOP_ACTIONS)
    codes = {getattr(flag, "code", "") for flag in flags}
    if "document_instruction_injection" in codes or "missing_document" in codes:
        return list(HOLD_ACTIONS)
    recommendation = result.recommendation if result is not None else None
    if recommendation is None:
        return list(HOLD_ACTIONS)
    value = recommendation.recommendation
    if value in {"decline", "escalate"}:
        return list(HARD_STOP_ACTIONS)
    if value in {"hold", "request_documents"}:
        return list(HOLD_ACTIONS)
    if value == "conditional_approve":
        return ["conditional_approve", "decline", "request_documents"]
    if value == "approve":
        return list(ALL_ACTIONS)
    return list(HOLD_ACTIONS)


def blocked_reason(
    result: CaseResult | None,
    flags: Sequence[FlagRecord] | Sequence[object],
) -> str | None:
    if _has_hard_stop(flags):
        return (
            "A hard-stop flag blocks approval and document requests. Decline the case; "
            "a hard stop can never be approved."
        )
    codes = {getattr(flag, "code", "") for flag in flags}
    if "document_instruction_injection" in codes:
        return (
            "Document-embedded instructions were found and quarantined. Approval is "
            "blocked; request documents or decline after a manual review."
        )
    if "missing_document" in codes:
        return (
            "Required packet documents are missing. Request documents or decline; "
            "funding cannot be approved from an incomplete packet."
        )
    if (
        result is not None
        and result.recommendation is not None
        and result.recommendation.recommendation == "hold"
    ):
        return (
            "The recommendation is hold. Only request documents or decline are "
            "available; approve is not offered."
        )
    return None


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
            counts: dict[Severity, int] = {"hard_stop": 0, "high": 0, "medium": 0, "low": 0}
            for flag in flags:
                counts[flag.severity] = counts.get(flag.severity, 0) + 1
            decision = (
                result.decision.decision
                if result is not None and result.decision is not None
                else (view.decision.decision if view.decision is not None else None)
            )
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
                    decision=decision,
                    flags=counts,
                    pending_approval=(
                        result is not None
                        and result.status == "pending_approval"
                        and view.decision is None
                    ),
                    decided=decision is not None,
                )
            )
        return items

    def _status(self, view: CaseView, result: CaseResult | None) -> QueueStatus:
        if view.decision is not None:
            return decision_status(view.decision.decision)
        if result is not None:
            if result.status == "failed":
                return "failed"
            if result.status == "pending_approval":
                return "pending_approval"
            if result.decision is not None:
                return decision_status(result.decision.decision)
        return cast(QueueStatus, view.status)

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
        if (
            view.decision is not None
            and result is not None
            and (result.decision is None or not result.audit_verified)
        ):
            result = self.runner.reconcile(case_id, view) or result
        try:
            audit = self.gateway.get_audit(case_id, limit=200).events
        except GatewayToolError:
            audit = list(result.audit) if result is not None else []
        flags = result.flags if result is not None else view.flags
        return CaseDetail(
            view=view,
            result=result,
            audit=list(audit),
            fields=result.fields() if result is not None else [],
            allowed_actions=allowed_actions(result, flags),
            blocker=blocked_reason(result, flags),
        )

    # -- actions ---------------------------------------------------------

    def _reconciled(self, case_id: str, view: CaseView) -> CaseResult | None:
        if view.decision is None:
            return None
        return self.runner.reconcile(case_id, view)

    def _reject_existing_decision(self, case_id: str, view: CaseView) -> None:
        if view.decision is not None:
            raise ConsoleError(
                "case_already_decided",
                f"Case {case_id!r} already has decision {view.decision.decision_id}.",
                status_code=409,
            )

    def run(self, case_id: str) -> CaseResult:
        try:
            view = self.gateway.read_case(case_id)
        except GatewayToolError as exc:
            raise ConsoleError(
                "case_not_found",
                f"Case {case_id!r} was not found.",
                status_code=404,
            ) from exc
        reconciled = self._reconciled(case_id, view)
        if reconciled is not None:
            return reconciled
        self._reject_existing_decision(case_id, view)
        existing = self.runner.get(case_id)
        if existing is not None and existing.decision is not None:
            return existing
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

    def retry(self, case_id: str) -> CaseResult:
        try:
            view = self.gateway.read_case(case_id)
        except GatewayToolError as exc:
            raise ConsoleError(
                "case_not_found",
                f"Case {case_id!r} was not found.",
                status_code=404,
            ) from exc
        reconciled = self._reconciled(case_id, view)
        if reconciled is not None:
            return reconciled
        self._reject_existing_decision(case_id, view)
        current = self.runner.get(case_id)
        if current is None:
            raise ConsoleError(
                "case_not_started",
                f"Case {case_id!r} has not been run.",
                status_code=409,
            )
        if current.status != "failed":
            return current
        self.runner.reset(case_id)
        result = self.runner.start(case_id)
        if result.status == "failed":
            raise ConsoleError(
                "case_run_failed",
                result.error or f"Case {case_id!r} could not be retried.",
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
        try:
            live = self.gateway.read_case(case_id)
        except GatewayToolError as exc:
            raise ConsoleError(exc.code, exc.message, status_code=502) from exc
        reconciled = self._reconciled(case_id, live)
        if reconciled is not None:
            return reconciled
        self._reject_existing_decision(case_id, live)
        if current.decision is not None:
            return current
        if current.status == "failed":
            raise ConsoleError(
                "case_failed",
                "The case run failed. Retry the run before deciding.",
                status_code=409,
            )
        if current.status != "pending_approval":
            raise ConsoleError(
                "case_not_pending",
                f"Case {case_id!r} is not waiting for a human decision.",
                status_code=409,
            )
        allowed = allowed_actions(current, current.flags)
        if decision not in allowed:
            raise ConsoleError(
                "decision_blocked",
                blocked_reason(current, current.flags)
                or f"The {decision} action is not available for this recommendation.",
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
    "decision_status",
    "decision_status_label",
    "recommendation_label",
]
