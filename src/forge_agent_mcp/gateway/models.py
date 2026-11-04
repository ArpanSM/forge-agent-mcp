"""Typed contracts shared by the gateway, the SQLite store and MCP clients."""

from __future__ import annotations

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..corpus.vocab import CaseType, Decision, Recommendation, Severity

CaseStatus = Literal[
    "pending_review",
    "extracting",
    "pending_approval",
    "awaiting_documents",
    "approved",
    "declined",
]
DocumentStatus = Literal["pending", "extracted"]
CrmStage = Literal["approved", "conditionally_approved", "declined", "awaiting_documents"]
AuditEventType = Literal[
    "read",
    "write",
    "write_committed",
    "write_denied",
    "idempotent_replay",
    "idempotency_conflict",
    "read_denied",
    "auth_denied",
]
AuditOutcome = Literal["ok", "denied", "error"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CarrierView(StrictModel):
    carrier_id: str
    legal_name: str
    dba: str
    ein: str
    mc_number: str
    dot_number: str
    address: str
    phone: str
    email: str
    owner_name: str


class CitationView(StrictModel):
    document_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    record_path: str | None = None

    @model_validator(mode="after")
    def _require_locator(self) -> CitationView:
        if not self.document_id and not self.record_path:
            raise ValueError("citation must include a document_id or record_path")
        return self


class ExtractedField(StrictModel):
    field: str = Field(min_length=1)
    value: str
    citation: CitationView


class DocumentBBox(StrictModel):
    field: str
    document_id: str
    page: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)


class DocumentPage(StrictModel):
    page: int = Field(ge=1)
    text: str


class DocumentRecord(StrictModel):
    tenant_id: str
    case_id: str
    document_id: str
    document_type: str
    title: str
    filename: str
    pages: int = Field(ge=1)
    content_hash: str
    sha256: str
    text: str
    page_text: list[DocumentPage]
    bboxes: list[DocumentBBox]
    status: DocumentStatus


class CaseRecord(StrictModel):
    tenant_id: str
    case_id: str
    case_type: CaseType
    carrier: CarrierView
    status: CaseStatus
    summary: str
    invoice_number: str | None = None
    invoice_content_hash: str | None = None
    document_count: int = Field(default=0, ge=0)


class StoredRecord(StrictModel):
    tenant_id: str
    case_id: str
    record_id: str
    record_type: str
    data: dict[str, Any]


class FlagRecord(StrictModel):
    code: str = Field(min_length=1)
    severity: Severity
    rule: str = Field(min_length=1)
    details: str = Field(min_length=1)
    evidence: CitationView


class ExtractionRecord(StrictModel):
    extraction_id: str
    case_id: str
    document_id: str
    fields: list[ExtractedField]
    recorded_by: str
    created_at: str


class RecommendationRecord(StrictModel):
    recommendation_id: str
    case_id: str
    recommendation: Recommendation
    rationale: str
    conditions: list[str]
    flags: list[FlagRecord]
    recorded_by: str
    created_at: str


class DecisionRecord(StrictModel):
    decision_id: str
    case_id: str
    decision: Decision
    rationale: str
    conditions: list[str]
    approver: str | None = None
    principal: str | None = None
    replayed: bool = False
    created_at: str


class CrmStageRecord(StrictModel):
    crm_stage_id: str
    case_id: str
    stage: CrmStage
    decision_id: str
    created_at: str


class ApprovalRecord(StrictModel):
    tenant_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    jti: str = Field(min_length=1)
    approver: str = Field(min_length=1)
    payload_hash: str = Field(min_length=64, max_length=64)
    expires_at: str
    used_at: str | None = None
    created_at: str


class IdempotencyRecord(StrictModel):
    tenant_id: str = Field(min_length=1)
    principal: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    key: str = Field(min_length=1)
    args_hash: str = Field(min_length=64, max_length=64)
    result: dict[str, Any]
    created_at: str


class AuditEvent(StrictModel):
    event_id: int
    tenant_id: str
    case_id: str | None = None
    tool: str
    event: AuditEventType
    outcome: AuditOutcome
    principal: str
    input_hash: str | None = None
    output_hash: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str


DataType = TypeVar("DataType")


class Envelope[DataType](StrictModel):
    ok: bool = True
    data: DataType | None = None
    error_code: str | None = None
    message: str | None = None


class PolicyThresholds(StrictModel):
    min_auto_liability: int = 1_000_000
    min_cargo_limit: int = 100_000
    min_authority_age_days: int = 183  # six months; matches the corpus label generator
    bank_change_window_days: int = 30
    high_flag_hold_threshold: int = 2


class PolicyFlag(StrictModel):
    code: str
    severity: Severity
    rule: str
    decision_effect: str


class PolicyDecisionRule(StrictModel):
    hard_stop: str
    high: str
    medium: str
    low: str
    clean: str


class PolicyPack(StrictModel):
    policy_id: str
    case_type: CaseType
    as_of: str
    thresholds: PolicyThresholds
    required_documents: list[str]
    required_records: list[str]
    flags: list[PolicyFlag]
    decision_rule: PolicyDecisionRule


class CaseDocumentView(StrictModel):
    document_id: str
    document_type: str
    title: str
    pages: int = Field(ge=1)
    content_hash: str
    status: DocumentStatus


class CaseView(StrictModel):
    tenant_id: str
    case_id: str
    case_type: CaseType
    status: CaseStatus
    carrier: CarrierView
    summary: str
    documents: list[CaseDocumentView]
    extraction_count: int = Field(ge=0)
    recommendation: RecommendationRecord | None = None
    flags: list[FlagRecord]
    decision: DecisionRecord | None = None
    crm_stage: CrmStageRecord | None = None


class DocumentView(StrictModel):
    document_id: str
    case_id: str
    document_type: str
    title: str
    text: str
    pages: list[DocumentPage]
    bboxes: list[DocumentBBox]
    content_hash: str
    sha256: str


class DuplicateMatch(StrictModel):
    kind: Literal["invoice_number", "document_hash"]
    value: str
    case_id: str
    document_id: str | None = None
    invoice_number: str | None = None
    load_number: str | None = None


class DuplicateView(StrictModel):
    case_id: str
    invoice_number: str | None = None
    document_hash: str | None = None
    duplicate: bool
    matches: list[DuplicateMatch]


class AuditView(StrictModel):
    case_id: str
    events: list[AuditEvent]


class ReadCaseInput(StrictModel):
    case_id: str = Field(min_length=1)


class GetDocumentInput(StrictModel):
    case_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)


class GetPolicyInput(StrictModel):
    case_id: str = Field(min_length=1)


class CheckDuplicateInput(StrictModel):
    case_id: str = Field(min_length=1)
    invoice_number: str | None = None
    document_hash: str | None = None


class RecordExtractionInput(StrictModel):
    case_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    fields: list[ExtractedField] = Field(min_length=1)


class RecordRecommendationInput(StrictModel):
    case_id: str = Field(min_length=1)
    recommendation: Recommendation
    rationale: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    flags: list[FlagRecord] = Field(default_factory=list)


class CreateDecisionInput(StrictModel):
    case_id: str = Field(min_length=1)
    decision: Decision
    rationale: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    approval_token: str
    idempotency_key: str = Field(min_length=1)


class GetAuditInput(StrictModel):
    case_id: str = Field(min_length=1)
    limit: int = Field(default=100, ge=1, le=500)
