"""The eight-stage LangGraph case runner.

Stage boundaries are graph nodes so a checkpoint shows exactly how far a case
got: intake/classify -> extract -> validate -> cross-check -> summarise ->
human approval -> execute via MCP -> audit/close. The full validators run in
``validate``; ``cross_check`` adds the gateway's tenant-wide duplicate check and
evidence reconciliation. Only the last three stages can write, and the decision
write is the gateway's single idempotent transaction.
"""

from __future__ import annotations

import operator
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..corpus.vocab import CaseType
from ..extraction.extractor import extract_case
from ..extraction.models import CaseExtraction
from ..gateway.approval import decision_payload_hash
from ..gateway.models import CaseView, DuplicateView, PolicyPack
from ..providers.base import ChatProvider
from ..rules.corpus import load_case_packet
from ..rules.engine import recommend, validate
from ..rules.models import CaseReview, Evidence, RecommendationResult, RuleFlag
from ..rules.validators import find_missing_documents
from .gateway import (
    CaseAlreadyDecidedError,
    DecisionCommand,
    GatewayClient,
    GatewayToolError,
)
from .models import (
    ApprovalAction,
    ApprovalDecision,
    ApprovalFailure,
    ApprovalRequest,
    CaseResult,
    decision_idempotency_key,
    gateway_fields,
    gateway_flag,
)
from .tokens import ApprovalTokenVault

RETRYABLE_APPROVAL_CODES = frozenset(
    {
        "approval_required",
        "approval_malformed",
        "approval_expired",
        "approval_mismatch",
        "approval_reused",
    }
)


class CaseGraphState(TypedDict, total=False):
    case_id: str
    thread_id: str
    transitions: Annotated[list[str], operator.add]
    case: dict[str, Any]
    policy: dict[str, Any]
    extraction: dict[str, Any]
    review: dict[str, Any]
    flags: list[dict[str, Any]]
    cross_check: dict[str, Any] | None
    recommendation: dict[str, Any]
    approval_request: dict[str, Any]
    approval: dict[str, Any]
    approval_failure: dict[str, Any]
    approval_invalid: bool
    approval_attempts: int
    decision: dict[str, Any]
    idempotency_key: str
    audit: list[dict[str, Any]]
    audit_verified: bool
    error: str | None


class CaseRunError(RuntimeError):
    """A case could not be started or resumed at this point in its lifecycle."""


def _thread_id(case_id: str) -> str:
    return f"case:{case_id}"


def _error_text(exc: Exception) -> str:
    if isinstance(exc, GatewayToolError):
        return f"{exc.code}: {exc.message}"
    return str(exc) or exc.__class__.__name__


def _evidence_gaps(extraction: CaseExtraction, packet: Any) -> list[str]:
    gaps: list[str] = []
    for missing in find_missing_documents(extraction, packet):
        if missing.details not in gaps:
            gaps.append(missing.details)
    for issue in extraction.issues:
        gap = f"{issue.document_id or 'case'}: {issue.message}"
        if gap not in gaps:
            gaps.append(gap)
    return gaps


def _summary(
    *,
    case_id: str,
    flags: list[RuleFlag],
    recommendation: RecommendationResult,
) -> str:
    if not flags:
        return (
            f"{case_id}: {recommendation.recommendation} "
            f"(decision {recommendation.decision}). {recommendation.rationale}"
        )
    codes = ", ".join(flag.code for flag in flags)
    return (
        f"{case_id}: {recommendation.recommendation} (decision {recommendation.decision}). "
        f"{len(flags)} rule flag(s): {codes}. {recommendation.rationale}"
    )


def _review(
    *,
    case_id: str,
    case_type: CaseType,
    as_of: date | None,
    extraction: CaseExtraction,
    packet: Any,
    flags: list[RuleFlag],
) -> CaseReview:
    recommendation = recommend(flags, evidence_gaps=_evidence_gaps(extraction, packet))
    return CaseReview(
        case_id=case_id,
        case_type=case_type,
        as_of=as_of,
        flags=flags,
        recommendation=recommendation,
        summary=_summary(case_id=case_id, flags=flags, recommendation=recommendation),
    )


def _verify_audit(events: list[Any], decision: dict[str, Any] | None) -> bool:
    """A committed decision must appear exactly once in an ordered audit chain."""

    if not events or decision is None:
        return False
    event_ids = [event.event_id for event in events]
    if event_ids != sorted(event_ids) or len(event_ids) != len(set(event_ids)):
        return False
    decision_id = decision.get("decision_id")
    commits = [
        event
        for event in events
        if event.event == "write_committed"
        and (event.detail or {}).get("decision_id") == decision_id
    ]
    return len(commits) == 1


def _approval_failure(
    *,
    code: str,
    message: str,
    attempts: int,
) -> dict[str, Any]:
    return {
        "approval_failure": ApprovalFailure(
            code=code,
            message=message,
            attempts=attempts,
        ).model_dump(mode="json"),
        "approval_attempts": attempts,
        "approval_invalid": False,
    }


def build_case_graph(
    gateway: GatewayClient,
    *,
    corpus_dir: str | Path,
    provider: ChatProvider | None = None,
    vault: ApprovalTokenVault | None = None,
) -> StateGraph:
    """Build the uncompiled eight-stage graph over one gateway client."""

    root = Path(corpus_dir)
    token_vault = vault or ApprovalTokenVault()

    def intake(state: CaseGraphState) -> dict[str, Any]:
        case_id = state["case_id"]
        try:
            case = gateway.read_case(case_id)
            policy = gateway.get_policy(case_id)
        except Exception as exc:  # noqa: BLE001 - recorded as a case failure
            return {"error": _error_text(exc), "transitions": ["intake"]}
        return {
            "case": case.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "transitions": ["intake"],
        }

    def extract(state: CaseGraphState) -> dict[str, Any]:
        case_id = state["case_id"]
        try:
            extracted = extract_case(case_id, root, provider=provider)
            if not extracted.ok or extracted.data is None:
                raise CaseRunError(extracted.message or "extraction failed")
            report = extracted.data
            for document in report.documents:
                fields = gateway_fields(report, document.classification.document_id)
                if fields:
                    gateway.record_extraction(
                        case_id,
                        document.classification.document_id,
                        fields,
                    )
        except Exception as exc:  # noqa: BLE001
            return {"error": _error_text(exc), "transitions": ["extract"]}
        return {
            "extraction": report.model_dump(mode="json"),
            "transitions": ["extract"],
        }

    def validate_node(state: CaseGraphState) -> dict[str, Any]:
        case_id = state["case_id"]
        try:
            extraction = CaseExtraction.model_validate(state["extraction"])
            case = CaseView.model_validate(state["case"])
            packet = load_case_packet(case_id, root)
            flags = validate(extraction, packet)
            review = _review(
                case_id=case_id,
                case_type=case.case_type,
                as_of=packet.as_of,
                extraction=extraction,
                packet=packet,
                flags=flags,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": _error_text(exc), "transitions": ["validate"]}
        return {
            "review": review.model_dump(mode="json"),
            "flags": [flag.model_dump(mode="json") for flag in flags],
            "recommendation": review.recommendation.model_dump(mode="json"),
            "transitions": ["validate"],
        }

    def cross_check(state: CaseGraphState) -> dict[str, Any]:
        case_id = state["case_id"]
        flags = [RuleFlag.model_validate(item) for item in state.get("flags") or []]
        cross: DuplicateView | None = None
        try:
            case = CaseView.model_validate(state["case"])
            if case.case_type == "invoice":
                cross = gateway.check_duplicate(case_id)
                if cross.duplicate and not any(flag.code == "duplicate_invoice" for flag in flags):
                    match = cross.matches[0]
                    flags.append(
                        RuleFlag(
                            code="duplicate_invoice",
                            severity="hard_stop",
                            rule_id="crosscheck.duplicate",
                            rule=(
                                "invoice number or document hash appears elsewhere in the tenant"
                            ),
                            details=(
                                "The gateway duplicate cross-check matched "
                                f"{match.kind} {match.value!r} on case {match.case_id}."
                            ),
                            evidence=Evidence(
                                record_id=f"crosscheck:{match.case_id}",
                                record_path=(
                                    f"crosscheck/{match.case_id}/"
                                    f"{match.invoice_number or match.value}"
                                ),
                            ),
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            return {"error": _error_text(exc), "transitions": ["cross_check"]}
        return {
            "flags": [flag.model_dump(mode="json") for flag in flags],
            "cross_check": cross.model_dump(mode="json") if cross is not None else None,
            "transitions": ["cross_check"],
        }

    def summarise(state: CaseGraphState) -> dict[str, Any]:
        case_id = state["case_id"]
        try:
            extraction = CaseExtraction.model_validate(state["extraction"])
            case = CaseView.model_validate(state["case"])
            policy = PolicyPack.model_validate(state["policy"])
            packet = load_case_packet(case_id, root)
            flags = [RuleFlag.model_validate(item) for item in state.get("flags") or []]
            review = _review(
                case_id=case_id,
                case_type=case.case_type,
                as_of=packet.as_of,
                extraction=extraction,
                packet=packet,
                flags=flags,
            )
            recommendation = review.recommendation
            gateway.record_recommendation(
                case_id,
                recommendation.recommendation,
                recommendation.rationale,
                recommendation.conditions,
                [gateway_flag(case_id=case_id, flag=flag) for flag in flags],
            )
            case = gateway.read_case(case_id)
            request = ApprovalRequest(
                case_id=case_id,
                thread_id=state.get("thread_id") or _thread_id(case_id),
                case_type=case.case_type,
                carrier_name=case.carrier.legal_name,
                summary=review.summary,
                recommendation=recommendation,
                flags=flags,
                documents=case.documents,
                policy=policy,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": _error_text(exc), "transitions": ["summarise"]}
        return {
            "case": case.model_dump(mode="json"),
            "review": review.model_dump(mode="json"),
            "flags": [flag.model_dump(mode="json") for flag in flags],
            "recommendation": recommendation.model_dump(mode="json"),
            "approval_request": request.model_dump(mode="json"),
            "transitions": ["summarise"],
        }

    def human_approval(state: CaseGraphState) -> dict[str, Any]:
        """Record the approval stage before the wait node interrupts."""

        del state
        return {
            "approval": None,
            "approval_invalid": False,
            "transitions": ["human_approval"],
        }

    def await_approval(state: CaseGraphState) -> dict[str, Any]:
        request = state.get("approval_request")
        if request is None:
            return {
                "error": "no approval request was produced",
                "transitions": [],
            }
        response = interrupt(request)
        try:
            decision = ApprovalDecision.model_validate(response)
        except Exception as exc:  # noqa: BLE001
            attempts = int(state.get("approval_attempts", 0)) + 1
            return {
                **_approval_failure(
                    code="invalid_approval",
                    message=f"invalid approval response: {_error_text(exc)}",
                    attempts=attempts,
                ),
                "approval_invalid": True,
                "approval": None,
            }
        return {
            "approval": decision.model_dump(mode="json"),
            "approval_failure": None,
            "approval_invalid": False,
        }

    def execute(state: CaseGraphState) -> dict[str, Any]:
        if state.get("error"):
            return {"transitions": ["execute"]}
        case_id = state["case_id"]
        approval = ApprovalDecision.model_validate(state["approval"])
        tokens = token_vault.take(approval.approval_reference)
        if tokens is None:
            attempts = int(state.get("approval_attempts", 0)) + 1
            return {
                **_approval_failure(
                    code="approval_required",
                    message=(
                        "The approval context is no longer available; request a fresh approval."
                    ),
                    attempts=attempts,
                ),
                "transitions": ["execute"],
            }
        command = DecisionCommand(
            case_id=case_id,
            decision=approval.action,
            rationale=approval.rationale,
            conditions=approval.conditions,
            approval_token=tokens.approval_token,
            idempotency_key=decision_idempotency_key(
                case_id=case_id,
                decision=approval.action,
                rationale=approval.rationale,
                conditions=approval.conditions,
            ),
            approval_jwt=tokens.approval_jwt,
        )
        try:
            decision = gateway.create_decision(command)
        except CaseAlreadyDecidedError as exc:
            return {
                "decision": exc.decision.model_dump(mode="json"),
                "approval_failure": None,
                "transitions": ["execute"],
            }
        except GatewayToolError as exc:
            if exc.code in RETRYABLE_APPROVAL_CODES:
                attempts = int(state.get("approval_attempts", 0)) + 1
                return {
                    **_approval_failure(
                        code=exc.code,
                        message=exc.message,
                        attempts=attempts,
                    ),
                    "transitions": ["execute"],
                }
            return {
                "error": _error_text(exc),
                "transitions": ["execute"],
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": _error_text(exc), "transitions": ["execute"]}
        return {
            "decision": decision.model_dump(mode="json"),
            "approval_failure": None,
            "transitions": ["execute"],
        }

    def audit_close(state: CaseGraphState) -> dict[str, Any]:
        case_id = state["case_id"]
        try:
            audit = gateway.get_audit(case_id, limit=200)
            case = gateway.read_case(case_id)
        except Exception as exc:  # noqa: BLE001
            return {
                "audit": [],
                "audit_verified": False,
                "error": state.get("error") or _error_text(exc),
                "transitions": ["audit_close"],
            }
        events = [event.model_dump(mode="json") for event in audit.events]
        return {
            "case": case.model_dump(mode="json"),
            "audit": events,
            "audit_verified": _verify_audit(audit.events, state.get("decision")),
            "transitions": ["audit_close"],
        }

    def failed(state: CaseGraphState) -> dict[str, Any]:
        del state
        return {"transitions": ["failed"]}

    def next_after(next_node: str) -> Callable[[CaseGraphState], str]:
        def route(state: CaseGraphState) -> str:
            return "failed" if state.get("error") else next_node

        return route

    def after_await(state: CaseGraphState) -> str:
        return "human_approval" if state.get("approval_invalid") else "execute"

    def after_execute(state: CaseGraphState) -> str:
        if state.get("error"):
            return "audit_close"
        if state.get("approval_failure"):
            return "human_approval"
        return "audit_close"

    builder = StateGraph(CaseGraphState)
    builder.add_node("intake", intake)
    builder.add_node("extract", extract)
    builder.add_node("validate", validate_node)
    builder.add_node("cross_check", cross_check)
    builder.add_node("summarise", summarise)
    builder.add_node("human_approval", human_approval)
    builder.add_node("await_approval", await_approval)
    builder.add_node("execute", execute)
    builder.add_node("audit_close", audit_close)
    builder.add_node("failed", failed)

    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", next_after("extract"), ["extract", "failed"])
    builder.add_conditional_edges("extract", next_after("validate"), ["validate", "failed"])
    builder.add_conditional_edges("validate", next_after("cross_check"), ["cross_check", "failed"])
    builder.add_conditional_edges("cross_check", next_after("summarise"), ["summarise", "failed"])
    builder.add_conditional_edges(
        "summarise", next_after("human_approval"), ["human_approval", "failed"]
    )
    builder.add_edge("human_approval", "await_approval")
    builder.add_conditional_edges("await_approval", after_await, ["execute", "human_approval"])
    builder.add_conditional_edges("execute", after_execute, ["audit_close", "human_approval"])
    builder.add_edge("audit_close", END)
    builder.add_edge("failed", END)
    return builder


class CaseRunner:
    """Compile, run and resume the case graph against a durable checkpointer."""

    def __init__(
        self,
        gateway: GatewayClient,
        *,
        corpus_dir: str | Path,
        checkpoint_path: str | Path,
        provider: ChatProvider | None = None,
    ) -> None:
        self.gateway = gateway
        self.corpus_dir = Path(corpus_dir)
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.checkpoint_path, check_same_thread=False)
        self._saver = SqliteSaver(self._connection)
        self._vault = ApprovalTokenVault()
        self._graph = build_case_graph(
            gateway,
            corpus_dir=self.corpus_dir,
            provider=provider,
            vault=self._vault,
        ).compile(checkpointer=self._saver)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _config(self, case_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": _thread_id(case_id)}}

    def _result(self, case_id: str) -> CaseResult | None:
        with self._lock:
            snapshot = self._graph.get_state(self._config(case_id))
        if not snapshot.values:
            return None
        return CaseResult.from_state(
            case_id=case_id,
            thread_id=_thread_id(case_id),
            state=dict(snapshot.values),
        )

    def get(self, case_id: str) -> CaseResult | None:
        return self._result(case_id)

    def state(self, case_id: str) -> dict[str, Any]:
        """Return the persisted graph state values for diagnostics and tests."""

        with self._lock:
            snapshot = self._graph.get_state(self._config(case_id))
        return dict(snapshot.values)

    def _mark_failed(self, case_id: str, error: str) -> None:
        try:
            with self._lock:
                self._graph.update_state(
                    self._config(case_id),
                    {"error": error, "transitions": ["failed"]},
                    as_node="failed",
                )
        except Exception:  # noqa: BLE001 - the caller still returns a failed result
            pass

    def start(self, case_id: str) -> CaseResult:
        existing = self._result(case_id)
        if existing is not None and existing.status != "not_started":
            return existing
        try:
            with self._lock:
                self._graph.invoke(
                    {
                        "case_id": case_id,
                        "thread_id": _thread_id(case_id),
                        "transitions": [],
                    },
                    config=self._config(case_id),
                )
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed case result
            error = _error_text(exc)
            self._mark_failed(case_id, error)
            result = self._result(case_id) or CaseResult(
                case_id=case_id,
                thread_id=_thread_id(case_id),
                status="failed",
                stage="failed",
            )
            return result.model_copy(update={"status": "failed", "error": error})
        result = self._result(case_id)
        if result is None:
            return CaseResult(
                case_id=case_id,
                thread_id=_thread_id(case_id),
                status="failed",
                stage="failed",
                error="the graph produced no state",
            )
        return result

    def resume(self, case_id: str, action: ApprovalAction | dict[str, Any]) -> CaseResult:
        current = self._result(case_id)
        if current is None:
            raise CaseRunError(f"case {case_id!r} has not been started")
        if current.decision is not None:
            return current
        if current.status == "failed":
            raise CaseRunError(f"case {case_id!r} failed and must be retried")
        if current.status != "pending_approval":
            raise CaseRunError(f"case {case_id!r} is not waiting for approval")

        try:
            view = self.gateway.read_case(case_id)
        except GatewayToolError:
            view = None
        if view is not None and view.decision is not None:
            reconciled = self.reconcile(case_id, view)
            if reconciled is not None:
                return reconciled

        action_model = (
            action if isinstance(action, ApprovalAction) else ApprovalAction.model_validate(action)
        )
        reference = f"approval:{uuid.uuid4().hex}"
        self._vault.put(
            reference,
            approval_token=action_model.approval_token,
            approval_jwt=action_model.approval_jwt,
        )
        payload = ApprovalDecision(
            action=action_model.action,
            approver=action_model.approver,
            rationale=action_model.rationale,
            conditions=action_model.conditions,
            note=action_model.note,
            approval_reference=reference,
            payload_hash=decision_payload_hash(
                case_id=case_id,
                decision=action_model.action,
                rationale=action_model.rationale,
                conditions=action_model.conditions,
            ),
        )
        try:
            with self._lock:
                latest = self._result(case_id)
                if latest is not None and latest.decision is not None:
                    return latest
                try:
                    live = self.gateway.read_case(case_id)
                except GatewayToolError:
                    live = None
                if live is not None and live.decision is not None:
                    reconciled = self.reconcile(case_id, live)
                    if reconciled is not None:
                        return reconciled
                if latest is None or latest.status != "pending_approval":
                    raise CaseRunError(f"case {case_id!r} is not waiting for approval")
                self._graph.invoke(
                    Command(resume=payload.model_dump(mode="json")),
                    config=self._config(case_id),
                )
        except CaseRunError:
            raise
        except Exception as exc:  # noqa: BLE001
            error = _error_text(exc)
            result = self._result(case_id) or current
            if result.decision is None:
                self._mark_failed(case_id, error)
                result = self._result(case_id) or result
            return result.model_copy(update={"status": "failed", "error": error})
        finally:
            self._vault.discard(reference)
        result = self._result(case_id)
        if result is None:
            return current.model_copy(update={"status": "failed", "error": "no state"})
        return result

    def reconcile(self, case_id: str, view: CaseView | None = None) -> CaseResult | None:
        """Adopt a gateway decision that committed before the checkpoint advanced."""

        current = self._result(case_id)
        if current is None:
            return None
        if current.decision is not None and current.audit_verified:
            return current
        view = view or self.gateway.read_case(case_id)
        if view.decision is None:
            return current
        audit = self.gateway.get_audit(case_id, limit=200)
        decision = view.decision.model_dump(mode="json")
        values: dict[str, Any] = {
            "decision": decision,
            "case": view.model_dump(mode="json"),
            "audit": [event.model_dump(mode="json") for event in audit.events],
            "audit_verified": _verify_audit(audit.events, decision),
            "approval_failure": None,
            "error": None,
            "transitions": ["reconcile"],
        }
        with self._lock:
            self._graph.update_state(
                self._config(case_id),
                values,
                as_node="audit_close",
            )
        return self._result(case_id)

    def reset(self, case_id: str) -> None:
        """Delete a failed thread so the case can be started again."""

        with self._lock:
            self._saver.delete_thread(_thread_id(case_id))


__all__ = [
    "CaseGraphState",
    "CaseRunError",
    "CaseRunner",
    "RETRYABLE_APPROVAL_CODES",
    "build_case_graph",
]
