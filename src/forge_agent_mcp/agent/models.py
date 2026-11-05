"""Typed state and results for the freight underwriting agent.

The graph stores plain JSON dictionaries in the LangGraph checkpointer; these
models are the validated boundary between that state, the console and the tests.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from ..corpus.vocab import CaseType, Decision, StrictModel
from ..extraction.models import CaseExtraction
from ..gateway.models import (
    AuditEvent,
    CaseDocumentView,
    CaseView,
    CitationView,
    DecisionRecord,
    DuplicateView,
    ExtractedField,
    FlagRecord,
    PolicyPack,
)
from ..gateway.store import decision_outcome
from ..rules.models import CaseReview, RecommendationResult, RuleFlag

CaseRunStatus = Literal[
    "not_started",
    "in_progress",
    "pending_approval",
    "approved",
    "declined",
    "awaiting_documents",
    "failed",
]


class ApprovalAction(StrictModel):
    """The human response the console resumes a paused graph with.

    ``approval_token`` is the console-minted HMAC token the gateway verifies;
    ``approval_jwt`` is a short-lived ``freight:approve`` bearer token minted for
    this one decision call. Both are created only after the reviewer acts.
    """

    action: Decision
    approver: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    note: str = ""
    approval_token: str = Field(min_length=1)
    approval_jwt: str = Field(min_length=1)


class ApprovalDecision(StrictModel):
    """The safe, persisted part of a human decision.

    The HMAC approval token and the ``freight:approve`` bearer token are held in
    the runner's transient token vault, never in graph state. This model keeps
    the vault reference and the payload hash for audit correlation.
    """

    action: Decision
    approver: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    note: str = ""
    approval_reference: str = Field(min_length=1)
    payload_hash: str = Field(min_length=64, max_length=64)


class ApprovalFailure(StrictModel):
    """A refused approval attempt that the reviewer can retry."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    attempts: int = Field(default=1, ge=1)


class ApprovalRequest(StrictModel):
    """The pending decision and evidence shown to a reviewer."""

    case_id: str
    thread_id: str
    case_type: CaseType
    carrier_name: str
    summary: str
    recommendation: RecommendationResult
    flags: list[RuleFlag] = Field(default_factory=list)
    documents: list[CaseDocumentView] = Field(default_factory=list)
    policy: PolicyPack


class ExtractedFieldView(StrictModel):
    """One extracted field flattened for the console with its citation."""

    label: str
    value: str
    confidence: float
    document_id: str
    document_type: str
    page: int
    bbox: list[float]
    quote: str


class CaseResult(StrictModel):
    """Everything one case run produced, including the evidence and audit."""

    case_id: str
    thread_id: str
    status: CaseRunStatus
    stage: str
    transitions: list[str] = Field(default_factory=list)
    case: CaseView | None = None
    policy: PolicyPack | None = None
    extraction: CaseExtraction | None = None
    review: CaseReview | None = None
    flags: list[RuleFlag] = Field(default_factory=list)
    recommendation: RecommendationResult | None = None
    cross_check: DuplicateView | None = None
    approval_request: ApprovalRequest | None = None
    approval: ApprovalDecision | None = None
    approval_failure: ApprovalFailure | None = None
    decision: DecisionRecord | None = None
    idempotency_key: str | None = None
    audit: list[AuditEvent] = Field(default_factory=list)
    audit_verified: bool = False
    error: str | None = None

    @property
    def case_type(self) -> CaseType | None:
        return self.case.case_type if self.case is not None else None

    def fields(self) -> list[ExtractedFieldView]:
        """Flatten extraction fields for a citation table, in packet order."""

        if self.extraction is None:
            return []
        fields: list[ExtractedFieldView] = []
        for document in self.extraction.documents:
            for key, value in document.fields.items():
                fields.append(
                    ExtractedFieldView(
                        label=key.replace("_", " ").capitalize(),
                        value=value.value,
                        confidence=value.confidence,
                        document_id=value.citation.document_id,
                        document_type=document.classification.document_type,
                        page=value.citation.page,
                        bbox=list(value.citation.bbox),
                        quote=value.citation.quote,
                    )
                )
        return fields

    @classmethod
    def from_state(
        cls,
        *,
        case_id: str,
        thread_id: str,
        state: dict[str, Any] | None,
    ) -> CaseResult:
        values = dict(state or {})
        status, stage = _status_and_stage(values)
        return cls(
            case_id=case_id,
            thread_id=thread_id,
            status=status,
            stage=stage,
            transitions=list(values.get("transitions") or []),
            case=_model(CaseView, values.get("case")),
            policy=_model(PolicyPack, values.get("policy")),
            extraction=_model(CaseExtraction, values.get("extraction")),
            review=_model(CaseReview, values.get("review")),
            flags=[RuleFlag.model_validate(item) for item in values.get("flags") or []],
            recommendation=_model(RecommendationResult, values.get("recommendation")),
            cross_check=_model(DuplicateView, values.get("cross_check")),
            approval_request=_model(ApprovalRequest, values.get("approval_request")),
            approval=_model(ApprovalDecision, values.get("approval")),
            approval_failure=_model(ApprovalFailure, values.get("approval_failure")),
            decision=_model(DecisionRecord, values.get("decision")),
            idempotency_key=values.get("idempotency_key"),
            audit=[AuditEvent.model_validate(item) for item in values.get("audit") or []],
            audit_verified=bool(values.get("audit_verified", False)),
            error=values.get("error"),
        )


def _model(model: Any, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, model):
        return value
    return model.model_validate(value)


def _status_and_stage(values: dict[str, Any]) -> tuple[CaseRunStatus, str]:
    transitions = list(values.get("transitions") or [])
    stage = transitions[-1] if transitions else "not_started"
    decision = values.get("decision")
    if decision is not None:
        decision_value = (
            decision.get("decision")
            if isinstance(decision, dict)
            else getattr(decision, "decision", None)
        )
        try:
            outcome = decision_outcome(decision_value)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - an unknown value is a failed run
            return "failed", stage
        return outcome.case_status, stage
    if values.get("error"):
        return "failed", stage
    if values.get("approval_request") is not None:
        return "pending_approval", stage
    if transitions:
        return "in_progress", stage
    return "not_started", stage


def decision_idempotency_key(
    *,
    case_id: str,
    decision: Decision,
    rationale: str,
    conditions: Sequence[str],
) -> str:
    """Return a deterministic key for one decision payload.

    The case and decision are the stable part; rationale and conditions are
    included so a changed review is never mistaken for a retry of the original
    payload. The gateway still scopes the key by tenant, principal and tool.
    """

    payload = json.dumps(
        {
            "case_id": case_id,
            "decision": decision,
            "rationale": rationale,
            "conditions": list(conditions),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"idem_{digest[:40]}"


def gateway_citation(
    *,
    case_id: str,
    evidence: Any,
) -> CitationView:
    """Map rule-engine evidence onto the gateway's citation contract."""

    document_id = getattr(evidence, "document_id", None)
    page = getattr(evidence, "page", None)
    bbox = getattr(evidence, "bbox", None)
    if document_id and page is not None and bbox is not None:
        return CitationView(document_id=document_id, page=page, bbox=list(bbox))
    if getattr(evidence, "record_path", None):
        return CitationView(record_path=evidence.record_path)
    if getattr(evidence, "record_id", None):
        return CitationView(record_path=evidence.record_id)
    if document_id:
        suffix = getattr(evidence, "missing_field", None) or "evidence"
        return CitationView(record_path=f"{document_id}#{suffix}")
    missing_type = getattr(evidence, "missing_document_type", None)
    if missing_type:
        return CitationView(record_path=f"case/{case_id}/missing_document/{missing_type}")
    return CitationView(record_path=f"case/{case_id}/evidence")


def gateway_flag(*, case_id: str, flag: RuleFlag) -> FlagRecord:
    """Map one rule flag onto the gateway's stored flag contract."""

    return FlagRecord(
        code=flag.code,
        severity=flag.severity,
        rule=flag.rule,
        details=flag.details,
        evidence=gateway_citation(case_id=case_id, evidence=flag.evidence),
    )


def gateway_fields(extraction: CaseExtraction, document_id: str) -> list[ExtractedField]:
    """Convert one document's extracted values into gateway write fields."""

    document = next(
        (item for item in extraction.documents if item.classification.document_id == document_id),
        None,
    )
    if document is None:
        return []
    return [
        ExtractedField(
            field=key,
            value=value.value,
            citation=gateway_citation(case_id=extraction.case_id, evidence=value.citation),
        )
        for key, value in document.fields.items()
    ]


__all__ = [
    "ApprovalAction",
    "ApprovalDecision",
    "ApprovalFailure",
    "ApprovalRequest",
    "CaseResult",
    "CaseRunStatus",
    "ExtractedFieldView",
    "decision_idempotency_key",
    "gateway_citation",
    "gateway_fields",
    "gateway_flag",
]
