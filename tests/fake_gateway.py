"""In-memory gateway used by agent and console tests.

It implements the same protocol as the real FastMCP HTTP client, including the
approval-token presence check and idempotent decision semantics, without a
network or a server. Rule and citation validation stay in the real gateway and
are covered by its own tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from forge_agent_mcp.agent.gateway import (
    CaseAlreadyDecidedError,
    DecisionCommand,
    GatewayToolError,
)
from forge_agent_mcp.corpus.generator import GenerationResult
from forge_agent_mcp.corpus.manifest import Manifest
from forge_agent_mcp.corpus.vocab import CaseType, Recommendation
from forge_agent_mcp.gateway.approval import decision_payload_hash
from forge_agent_mcp.gateway.models import (
    AuditEvent,
    AuditView,
    CarrierView,
    CaseDocumentView,
    CaseView,
    CrmStageRecord,
    DecisionRecord,
    DocumentPage,
    DocumentView,
    DuplicateView,
    ExtractedField,
    ExtractionRecord,
    FlagRecord,
    PolicyPack,
    RecommendationRecord,
)
from forge_agent_mcp.gateway.policies import build_policy_pack
from forge_agent_mcp.gateway.store import decision_outcome

TENANT = "tenant_northwind"
APPROVER = "underwriter@example.test"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class FakeGateway:
    """Small typed stand-in for the MCP gateway over one tenant."""

    def __init__(
        self,
        *,
        cases: dict[str, CaseView],
        policies: dict[CaseType, PolicyPack],
        tenant_id: str = TENANT,
    ) -> None:
        self.tenant_id = tenant_id
        self._cases = cases
        self._policies = policies
        self._events: dict[str, list[AuditEvent]] = {case_id: [] for case_id in cases}
        self._next_event_id = 1
        self._recommendations: dict[str, RecommendationRecord] = {}
        self._decisions: dict[str, DecisionRecord] = {}
        self._stages: dict[str, CrmStageRecord] = {}
        self._extractions: dict[str, int] = {case_id: 0 for case_id in cases}
        self._idempotency: dict[tuple[str, str], tuple[DecisionRecord, str]] = {}
        self.extraction_calls: list[tuple[str, str, list[ExtractedField]]] = []
        self.recommendation_calls: list[tuple[str, Recommendation, list[FlagRecord]]] = []
        self.decision_calls: list[dict[str, Any]] = []
        self.duplicate_matches: dict[str, DuplicateView] = {}
        self.approval_failures: list[tuple[str, str]] = []
        self.recommendation_failures: list[tuple[str, str]] = []

    def fail_next_approval(self, code: str, message: str) -> None:
        """Queue one simulated gateway approval refusal."""

        self.approval_failures.append((code, message))

    def fail_next_recommendation(self, code: str, message: str) -> None:
        """Queue one simulated gateway case-state refusal."""

        self.recommendation_failures.append((code, message))

    # -- construction ----------------------------------------------------

    @classmethod
    def from_corpus(
        cls,
        manifest: Manifest,
        corpus: GenerationResult,
        *,
        case_ids: list[str] | None = None,
        tenant_id: str = TENANT,
    ) -> FakeGateway:
        selected = set(case_ids or [case.case_id for case in corpus.index.cases])
        cases: dict[str, CaseView] = {}
        policies: dict[CaseType, PolicyPack] = {}
        for case in corpus.index.cases:
            if case.case_id not in selected:
                continue
            carrier = manifest.carrier_by_id[case.carrier_id]
            documents = [
                CaseDocumentView(
                    document_id=document.document_id,
                    document_type=document.document_type,
                    title=document.title,
                    pages=document.pages,
                    content_hash=document.content_hash,
                    status="pending",
                )
                for document in case.documents
            ]
            cases[case.case_id] = CaseView(
                tenant_id=tenant_id,
                case_id=case.case_id,
                case_type=case.case_type,
                status="pending_review",
                carrier=CarrierView(
                    carrier_id=carrier.carrier_id,
                    legal_name=carrier.legal_name,
                    dba=carrier.dba,
                    ein=carrier.ein,
                    mc_number=carrier.mc_number,
                    dot_number=carrier.dot_number,
                    address=carrier.address.one_line(),
                    phone=carrier.phone,
                    email=carrier.email,
                    owner_name=carrier.owner_name,
                ),
                summary="",
                documents=documents,
                extraction_count=0,
                recommendation=None,
                flags=[],
                decision=None,
                crm_stage=None,
            )
            policies.setdefault(case.case_type, build_policy_pack(manifest, case.case_type))
        return cls(cases=cases, policies=policies, tenant_id=tenant_id)

    # -- reads -----------------------------------------------------------

    def read_case(self, case_id: str) -> CaseView:
        view = self._cases.get(case_id)
        if view is None:
            raise GatewayToolError("case_not_found", f"Case {case_id!r} does not exist.")
        recommendation = self._recommendations.get(case_id)
        decision = self._decisions.get(case_id)
        stage = self._stages.get(case_id)
        return view.model_copy(
            update={
                "extraction_count": self._extractions[case_id],
                "recommendation": recommendation,
                "flags": recommendation.flags if recommendation else [],
                "decision": decision,
                "crm_stage": stage,
            }
        )

    def get_document(self, case_id: str, document_id: str) -> DocumentView:
        view = self.read_case(case_id)
        document = next((item for item in view.documents if item.document_id == document_id), None)
        if document is None:
            raise GatewayToolError(
                "document_not_found", f"Document {document_id!r} is not part of this case."
            )
        text = f"{document.title} ({document.document_id})"
        return DocumentView(
            document_id=document.document_id,
            case_id=case_id,
            document_type=document.document_type,
            title=document.title,
            text=text,
            pages=[DocumentPage(page=1, text=text)],
            bboxes=[],
            content_hash=document.content_hash,
            sha256=document.content_hash,
        )

    def get_policy(self, case_id: str) -> PolicyPack:
        view = self.read_case(case_id)
        return self._policies[view.case_type]

    def check_duplicate(
        self,
        case_id: str,
        invoice_number: str | None = None,
        document_hash: str | None = None,
    ) -> DuplicateView:
        view = self.read_case(case_id)
        del view
        override = self.duplicate_matches.get(case_id)
        if override is not None:
            return override
        return DuplicateView(
            case_id=case_id,
            invoice_number=invoice_number,
            document_hash=document_hash,
            duplicate=False,
            matches=[],
        )

    def get_audit(self, case_id: str, limit: int = 200) -> AuditView:
        if case_id not in self._events:
            raise GatewayToolError("case_not_found", f"Case {case_id!r} does not exist.")
        return AuditView(case_id=case_id, events=self._events[case_id][:limit])

    # -- case-state writes ----------------------------------------------

    def record_extraction(
        self,
        case_id: str,
        document_id: str,
        fields: list[ExtractedField],
    ) -> ExtractionRecord:
        view = self.read_case(case_id)
        if not any(document.document_id == document_id for document in view.documents):
            raise GatewayToolError(
                "document_not_found", f"Document {document_id!r} is not part of this case."
            )
        record = ExtractionRecord(
            extraction_id=f"ext_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            document_id=document_id,
            fields=fields,
            recorded_by="agent-underwriter",
            created_at=_now(),
        )
        self.extraction_calls.append((case_id, document_id, fields))
        self._extractions[case_id] += 1
        self._cases[case_id] = self._cases[case_id].model_copy(
            update={
                "documents": [
                    document.model_copy(update={"status": "extracted"})
                    if document.document_id == document_id
                    else document
                    for document in view.documents
                ],
                "status": "extracting",
            }
        )
        self._append(
            case_id,
            tool="record_extraction",
            event="write",
            outcome="ok",
        )
        return record

    def record_recommendation(
        self,
        case_id: str,
        recommendation: Recommendation,
        rationale: str,
        conditions: list[str],
        flags: list[FlagRecord],
    ) -> RecommendationRecord:
        if self.recommendation_failures:
            code, message = self.recommendation_failures.pop(0)
            self._append(
                case_id,
                tool="record_recommendation",
                event="write_denied",
                outcome="denied",
                detail={"error_code": code, "message": message},
            )
            raise GatewayToolError(code, message)
        record = RecommendationRecord(
            recommendation_id=f"rec_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            recommendation=recommendation,
            rationale=rationale,
            conditions=conditions,
            flags=flags,
            recorded_by="agent-underwriter",
            created_at=_now(),
        )
        self._recommendations[case_id] = record
        self.recommendation_calls.append((case_id, recommendation, flags))
        self._append(
            case_id,
            tool="record_recommendation",
            event="write",
            outcome="ok",
        )
        return record

    def create_decision(self, command: DecisionCommand) -> DecisionRecord:
        case_id = command.case_id
        self.decision_calls.append(
            {
                "case_id": case_id,
                "decision": command.decision,
                "rationale": command.rationale,
                "conditions": command.conditions,
                "approval_token": command.approval_token,
                "idempotency_key": command.idempotency_key,
                "approval_jwt": command.approval_jwt,
            }
        )
        if self.approval_failures:
            code, message = self.approval_failures.pop(0)
            self._append(
                case_id,
                tool="create_decision",
                event="write_denied",
                outcome="denied",
                detail={"error_code": code, "message": message},
            )
            raise GatewayToolError(code, message)
        if not command.approval_token.strip() or not command.approval_jwt.strip():
            self._append(
                case_id,
                tool="create_decision",
                event="write_denied",
                outcome="denied",
                detail={"error_code": "approval_required"},
            )
            raise GatewayToolError(
                "approval_required", "A console-minted approval token is required."
            )
        payload_hash = decision_payload_hash(
            case_id=case_id,
            decision=command.decision,
            rationale=command.rationale,
            conditions=command.conditions,
        )
        existing = self._idempotency.get((case_id, command.idempotency_key))
        if existing is not None:
            stored, stored_hash = existing
            if stored_hash != payload_hash:
                self._append(
                    case_id,
                    tool="create_decision",
                    event="idempotency_conflict",
                    outcome="denied",
                    detail={"error_code": "idempotency_conflict"},
                )
                raise GatewayToolError(
                    "idempotency_conflict",
                    "This idempotency key was already used with different arguments.",
                )
            self._append(
                case_id,
                tool="create_decision",
                event="idempotent_replay",
                outcome="ok",
            )
            return stored.model_copy(update={"replayed": True})

        committed = self._decisions.get(case_id)
        if committed is not None:
            self._append(
                case_id,
                tool="create_decision",
                event="write_denied",
                outcome="denied",
                detail={
                    "error_code": "case_already_decided",
                    "decision_id": committed.decision_id,
                },
            )
            raise CaseAlreadyDecidedError(committed)

        outcome = decision_outcome(command.decision)
        record = DecisionRecord(
            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            decision=command.decision,
            rationale=command.rationale,
            conditions=command.conditions,
            approver=APPROVER,
            principal="agent-underwriter",
            created_at=_now(),
        )
        self._decisions[case_id] = record
        self._stages[case_id] = CrmStageRecord(
            crm_stage_id=f"crm_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            stage=outcome.crm_stage,
            decision_id=record.decision_id,
            created_at=record.created_at,
        )
        self._cases[case_id] = self._cases[case_id].model_copy(
            update={"status": outcome.case_status}
        )
        self._idempotency[(case_id, command.idempotency_key)] = (record, payload_hash)
        self._append(
            case_id,
            tool="create_decision",
            event="write_committed",
            outcome="ok",
            detail={
                "decision_id": record.decision_id,
                "decision": command.decision,
                "crm_stage": outcome.crm_stage,
                "case_status": outcome.case_status,
                "approver": APPROVER,
                "idempotency_key": command.idempotency_key,
                "payload_hash": payload_hash,
            },
        )
        return record

    # -- audit -----------------------------------------------------------

    def _append(
        self,
        case_id: str,
        *,
        tool: str,
        event: str,
        outcome: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        event_row = AuditEvent(
            event_id=self._next_event_id,
            tenant_id=self.tenant_id,
            case_id=case_id,
            tool=tool,
            event=event,  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            principal="agent-underwriter",
            detail=detail or {},
            created_at=_now(),
        )
        self._next_event_id += 1
        self._events[case_id].append(event_row)


__all__ = ["APPROVER", "FakeGateway", "TENANT"]
