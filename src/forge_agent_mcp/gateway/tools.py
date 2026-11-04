"""Typed MCP tools exposed by the freight gateway."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations
from pydantic import Field

from ..corpus.pdf import PAGE_HEIGHT, PAGE_WIDTH
from ..corpus.vocab import Decision, Recommendation
from .approval import (
    CONSOLE_ISSUER,
    REQUIRED_CODE,
    REUSED_CODE,
    ApprovalTokenError,
    decision_payload_hash,
    verify_approval_token,
)
from .auth import (
    APPROVE_SCOPE,
    READ_SCOPE,
    WRITE_SCOPE,
    access_token_principal,
    access_token_tenant,
)
from .models import (
    AuditEventType,
    AuditView,
    CaseDocumentView,
    CaseRecord,
    CaseView,
    CheckDuplicateInput,
    CreateDecisionInput,
    DecisionRecord,
    DocumentRecord,
    DocumentView,
    DuplicateMatch,
    DuplicateView,
    Envelope,
    ExtractedField,
    ExtractionRecord,
    FlagRecord,
    GetAuditInput,
    GetDocumentInput,
    GetPolicyInput,
    PolicyPack,
    ReadCaseInput,
    RecommendationRecord,
    RecordExtractionInput,
    RecordRecommendationInput,
)
from .store import ApprovalReusedError, GatewayStore, IdempotencyConflictError

CaseEnvelope = Envelope[CaseView]
DocumentEnvelope = Envelope[DocumentView]
PolicyEnvelope = Envelope[PolicyPack]
DuplicateEnvelope = Envelope[DuplicateView]
ExtractionEnvelope = Envelope[ExtractionRecord]
RecommendationEnvelope = Envelope[RecommendationRecord]
DecisionEnvelope = Envelope[DecisionRecord]
AuditEnvelope = Envelope[AuditView]


def _read_annotations(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def _write_annotations(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )


def _hash(payload: Any) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _failure(model: Any, error_code: str, message: str) -> ToolResult:
    envelope = model(ok=False, data=None, error_code=error_code, message=message)
    return ToolResult(
        content=envelope.model_dump_json(),
        structured_content=envelope.model_dump(mode="json"),
        is_error=True,
    )


@dataclass(frozen=True)
class RequestContext:
    principal: str
    tenant_id: str | None
    authenticated: bool


def _request_context() -> RequestContext:
    token = get_access_token()
    return RequestContext(
        principal=access_token_principal(token),
        tenant_id=access_token_tenant(token),
        authenticated=token is not None,
    )


@dataclass(frozen=True)
class CaseAccess:
    case: CaseRecord | None
    tenant_id: str
    audit_tenant: str
    error_code: str | None = None
    message: str | None = None


def _access_case(store: GatewayStore, context: RequestContext, case_id: str) -> CaseAccess:
    audit_tenant = context.tenant_id or "system"
    if context.authenticated and context.tenant_id is None:
        return CaseAccess(
            case=None,
            tenant_id="unknown",
            audit_tenant=audit_tenant,
            error_code="tenant_mismatch",
            message="The access token has no tenant claim.",
        )

    owners = store.case_tenants(case_id)
    if context.tenant_id is None:
        if not owners:
            return CaseAccess(
                case=None,
                tenant_id="unknown",
                audit_tenant=audit_tenant,
                error_code="case_not_found",
                message=f"Case {case_id!r} does not exist for this tenant.",
            )
        if len(owners) > 1:
            return CaseAccess(
                case=None,
                tenant_id="unknown",
                audit_tenant=audit_tenant,
                error_code="tenant_ambiguous",
                message=(
                    "This case id exists in more than one tenant; use HTTP with a tenant token."
                ),
            )
        tenant_id = owners[0]
    elif context.tenant_id in owners:
        tenant_id = context.tenant_id
    elif owners:
        return CaseAccess(
            case=None,
            tenant_id=owners[0],
            audit_tenant=context.tenant_id,
            error_code="tenant_mismatch",
            message="The authenticated tenant does not own this case.",
        )
    else:
        return CaseAccess(
            case=None,
            tenant_id=context.tenant_id,
            audit_tenant=context.tenant_id,
            error_code="case_not_found",
            message=f"Case {case_id!r} does not exist for this tenant.",
        )

    case = store.get_case(tenant_id, case_id)
    if case is None:
        return CaseAccess(
            case=None,
            tenant_id=tenant_id,
            audit_tenant=audit_tenant,
            error_code="case_not_found",
            message=f"Case {case_id!r} does not exist for this tenant.",
        )
    return CaseAccess(
        case=case,
        tenant_id=case.tenant_id,
        audit_tenant=case.tenant_id,
    )


def _audit_failure(
    store: GatewayStore,
    *,
    tenant_id: str,
    case_id: str,
    tool: str,
    event: AuditEventType,
    principal: str,
    error_code: str,
    message: str,
    request: Any,
) -> None:
    store.append_audit(
        tenant_id=tenant_id,
        case_id=case_id,
        tool=tool,
        event=event,
        outcome="error",
        principal=principal,
        input_hash=_hash(request),
        detail={"error_code": error_code, "message": message},
    )


def _audit_success(
    store: GatewayStore,
    *,
    tenant_id: str,
    case_id: str,
    tool: str,
    event: AuditEventType,
    principal: str,
    request: Any,
    envelope: Any,
) -> None:
    store.append_audit(
        tenant_id=tenant_id,
        case_id=case_id,
        tool=tool,
        event=event,
        outcome="ok",
        principal=principal,
        input_hash=_hash(request),
        output_hash=_hash(envelope),
    )


def _audit_decision_denied(
    store: GatewayStore,
    *,
    tenant_id: str,
    case_id: str,
    principal: str,
    request: CreateDecisionInput,
    error_code: str,
    message: str,
    event: AuditEventType = "write_denied",
) -> None:
    store.append_audit(
        tenant_id=tenant_id,
        case_id=case_id,
        tool="create_decision",
        event=event,
        outcome="denied",
        principal=principal,
        input_hash=_hash(request),
        detail={
            "error_code": error_code,
            "message": message,
            "idempotency_key": request.idempotency_key,
        },
    )


def _audit_decision_replay(
    store: GatewayStore,
    *,
    tenant_id: str,
    case_id: str,
    principal: str,
    request: CreateDecisionInput,
    envelope: Any,
) -> None:
    store.append_audit(
        tenant_id=tenant_id,
        case_id=case_id,
        tool="create_decision",
        event="idempotent_replay",
        outcome="ok",
        principal=principal,
        input_hash=_hash(request),
        output_hash=_hash(envelope),
        detail={
            "decision_id": envelope.data.decision_id,
            "idempotency_key": request.idempotency_key,
        },
    )


def _case_summary(
    case: CaseRecord,
    documents: list[CaseDocumentView],
    extraction_count: int,
    recommendation: RecommendationRecord | None,
    flags: list[FlagRecord],
) -> str:
    case_label = case.case_type.replace("_", " ").capitalize()
    parts = [
        f"{case_label} case for {case.carrier.legal_name}.",
        f"{len(documents)} documents and {extraction_count} extraction(s) recorded.",
        f"Status: {case.status.replace('_', ' ')}.",
    ]
    if recommendation is not None:
        parts.append(f"Current recommendation: {recommendation.recommendation}.")
    if flags:
        parts.append(
            "Current flags: " + ", ".join(f"{flag.code} ({flag.severity})" for flag in flags) + "."
        )
    return " ".join(parts)


def _case_view(store: GatewayStore, case: CaseRecord) -> CaseView:
    documents = [
        CaseDocumentView(
            document_id=document.document_id,
            document_type=document.document_type,
            title=document.title,
            pages=document.pages,
            content_hash=document.content_hash,
            status=document.status,
        )
        for document in store.list_documents(case.tenant_id, case.case_id)
    ]
    recommendation = store.latest_recommendation(case.tenant_id, case.case_id)
    flags = store.list_flags(case.tenant_id, case.case_id)
    extraction_count = store.count_extractions(case.tenant_id, case.case_id)
    return CaseView(
        tenant_id=case.tenant_id,
        case_id=case.case_id,
        case_type=case.case_type,
        status=case.status,
        carrier=case.carrier,
        summary=_case_summary(case, documents, extraction_count, recommendation, flags),
        documents=documents,
        extraction_count=extraction_count,
        recommendation=recommendation,
        flags=flags,
        decision=store.latest_decision(case.tenant_id, case.case_id),
        crm_stage=store.latest_crm_stage(case.tenant_id, case.case_id),
    )


def _duplicate_matches(
    store: GatewayStore,
    *,
    tenant_id: str,
    case: CaseRecord,
    invoice_number: str | None,
    document_hash: str | None,
) -> list[DuplicateMatch]:
    matches: list[DuplicateMatch] = []
    seen: set[tuple[str, str, str]] = set()

    def add(match: DuplicateMatch) -> None:
        key = (match.kind, match.value, match.case_id)
        if key not in seen:
            seen.add(key)
            matches.append(match)

    if invoice_number:
        for other in store.list_cases(tenant_id):
            if other.case_id != case.case_id and other.invoice_number == invoice_number:
                add(
                    DuplicateMatch(
                        kind="invoice_number",
                        value=invoice_number,
                        case_id=other.case_id,
                        invoice_number=other.invoice_number,
                    )
                )

    for record in store.list_records(tenant_id, record_type="prior_invoices"):
        for line in record.data.get("invoices", []):
            if invoice_number and line.get("invoice_number") == invoice_number:
                add(
                    DuplicateMatch(
                        kind="invoice_number",
                        value=invoice_number,
                        case_id=record.case_id,
                        invoice_number=line.get("invoice_number"),
                        load_number=line.get("load_number"),
                    )
                )
            if document_hash and line.get("document_hash") == document_hash:
                add(
                    DuplicateMatch(
                        kind="document_hash",
                        value=document_hash,
                        case_id=record.case_id,
                        document_id=record.record_id,
                        invoice_number=line.get("invoice_number"),
                        load_number=line.get("load_number"),
                    )
                )

    if document_hash:
        for other in store.list_cases(tenant_id):
            if other.case_id == case.case_id:
                continue
            for document in store.list_documents(tenant_id, other.case_id):
                if document.content_hash == document_hash:
                    add(
                        DuplicateMatch(
                            kind="document_hash",
                            value=document_hash,
                            case_id=other.case_id,
                            document_id=document.document_id,
                            invoice_number=other.invoice_number,
                        )
                    )
    return matches


def _validate_citation(field_document_id: str, field: Any, document: DocumentRecord) -> str | None:
    citation = field.citation
    if citation.document_id != field_document_id:
        return "Citation document_id must match the extracted document."
    if citation.page is None:
        return "Citation must include a page."
    if citation.page > document.pages:
        return "Citation page is outside the document."
    if citation.bbox is None:
        return "Citation must include a bounding box."
    if not _bbox_on_page(citation.bbox):
        return "Citation bounding box is outside the page."
    return None


def _bbox_on_page(bbox: list[float]) -> bool:
    if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
        return False
    x1, y1, x2, y2 = bbox
    return 0 <= x1 < x2 <= PAGE_WIDTH and 0 <= y1 < y2 <= PAGE_HEIGHT


def _validate_evidence(
    store: GatewayStore,
    *,
    tenant_id: str,
    case_id: str,
    flags: list[FlagRecord],
) -> str | None:
    for flag in flags:
        evidence = flag.evidence
        if evidence.document_id is None:
            if evidence.record_path:
                continue
            return f"Flag {flag.code!r} needs document or record evidence."
        document = store.get_document(tenant_id, case_id, evidence.document_id)
        if document is None:
            return f"Flag {flag.code!r} cites document {evidence.document_id!r} outside this case."
        if evidence.page is None or not 1 <= evidence.page <= document.pages:
            return (
                f"Flag {flag.code!r} cites page {evidence.page!r} outside {evidence.document_id!r}."
            )
        if evidence.bbox is None or not _bbox_on_page(evidence.bbox):
            return f"Flag {flag.code!r} has a bounding box outside the cited page."
    return None


def register_tools(
    mcp: FastMCP,
    store: GatewayStore,
    *,
    approval_secret: str,
    approval_audience: str,
    approval_issuer: str = CONSOLE_ISSUER,
) -> None:
    """Register the eight typed tools in deterministic order."""

    @mcp.tool(
        name="read_case",
        description=(
            "Read a case, its carrier, document metadata and current extraction, "
            "recommendation and decision state."
        ),
        auth=require_scopes(READ_SCOPE),
        output_schema=CaseEnvelope.model_json_schema(),
        annotations=_read_annotations("Read case"),
    )
    async def read_case(
        case_id: Annotated[str, Field(min_length=1)],
    ) -> CaseEnvelope | ToolResult:
        context = _request_context()
        request = ReadCaseInput(case_id=case_id)
        access = _access_case(store, context, request.case_id)
        if access.case is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="read_case",
                event="read_denied",
                principal=context.principal,
                error_code=access.error_code or "case_not_found",
                message=access.message or "Case not found.",
                request=request,
            )
            return _failure(
                CaseEnvelope, access.error_code or "case_not_found", access.message or ""
            )
        envelope = CaseEnvelope(ok=True, data=_case_view(store, access.case))
        _audit_success(
            store,
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            tool="read_case",
            event="read",
            principal=context.principal,
            request=request,
            envelope=envelope,
        )
        return envelope

    @mcp.tool(
        name="get_document",
        description=(
            "Read one synthetic document's text, pages, citation bounding boxes and content hash."
        ),
        auth=require_scopes(READ_SCOPE),
        output_schema=DocumentEnvelope.model_json_schema(),
        annotations=_read_annotations("Get document"),
    )
    async def get_document(
        case_id: Annotated[str, Field(min_length=1)],
        document_id: Annotated[str, Field(min_length=1)],
    ) -> DocumentEnvelope | ToolResult:
        context = _request_context()
        request = GetDocumentInput(case_id=case_id, document_id=document_id)
        access = _access_case(store, context, request.case_id)
        if access.case is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="get_document",
                event="read_denied",
                principal=context.principal,
                error_code=access.error_code or "case_not_found",
                message=access.message or "Case not found.",
                request=request,
            )
            return _failure(
                DocumentEnvelope, access.error_code or "case_not_found", access.message or ""
            )
        document = store.get_document(access.tenant_id, request.case_id, request.document_id)
        if document is None:
            message = f"Document {request.document_id!r} is not part of this case."
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="get_document",
                event="read_denied",
                principal=context.principal,
                error_code="document_not_found",
                message=message,
                request=request,
            )
            return _failure(DocumentEnvelope, "document_not_found", message)
        envelope = DocumentEnvelope(
            ok=True,
            data=DocumentView(
                document_id=document.document_id,
                case_id=document.case_id,
                document_type=document.document_type,
                title=document.title,
                text=document.text,
                pages=document.page_text,
                bboxes=document.bboxes,
                content_hash=document.content_hash,
                sha256=document.sha256,
            ),
        )
        _audit_success(
            store,
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            tool="get_document",
            event="read",
            principal=context.principal,
            request=request,
            envelope=envelope,
        )
        return envelope

    @mcp.tool(
        name="get_policy",
        description="Read the policy pack, thresholds and decision rule for a case type.",
        auth=require_scopes(READ_SCOPE),
        output_schema=PolicyEnvelope.model_json_schema(),
        annotations=_read_annotations("Get policy"),
    )
    async def get_policy(
        case_id: Annotated[str, Field(min_length=1)],
    ) -> PolicyEnvelope | ToolResult:
        context = _request_context()
        request = GetPolicyInput(case_id=case_id)
        access = _access_case(store, context, request.case_id)
        if access.case is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="get_policy",
                event="read_denied",
                principal=context.principal,
                error_code=access.error_code or "case_not_found",
                message=access.message or "Case not found.",
                request=request,
            )
            return _failure(
                PolicyEnvelope, access.error_code or "case_not_found", access.message or ""
            )
        policy = store.get_policy(access.tenant_id, access.case.case_type)
        if policy is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="get_policy",
                event="read_denied",
                principal=context.principal,
                error_code="policy_not_found",
                message="No policy pack is configured for this case type.",
                request=request,
            )
            return _failure(
                PolicyEnvelope,
                "policy_not_found",
                "No policy pack is configured for this case type.",
            )
        envelope = PolicyEnvelope(ok=True, data=policy)
        _audit_success(
            store,
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            tool="get_policy",
            event="read",
            principal=context.principal,
            request=request,
            envelope=envelope,
        )
        return envelope

    @mcp.tool(
        name="check_duplicate",
        description=(
            "Check the current invoice number and document hash against prior invoices "
            "and documents in the tenant."
        ),
        auth=require_scopes(READ_SCOPE),
        output_schema=DuplicateEnvelope.model_json_schema(),
        annotations=_read_annotations("Check duplicate"),
    )
    async def check_duplicate(
        case_id: Annotated[str, Field(min_length=1)],
        invoice_number: str | None = None,
        document_hash: str | None = None,
    ) -> DuplicateEnvelope | ToolResult:
        context = _request_context()
        request = CheckDuplicateInput(
            case_id=case_id,
            invoice_number=invoice_number,
            document_hash=document_hash,
        )
        access = _access_case(store, context, request.case_id)
        if access.case is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="check_duplicate",
                event="read_denied",
                principal=context.principal,
                error_code=access.error_code or "case_not_found",
                message=access.message or "Case not found.",
                request=request,
            )
            return _failure(
                DuplicateEnvelope, access.error_code or "case_not_found", access.message or ""
            )
        effective_number = request.invoice_number or access.case.invoice_number
        effective_hash = request.document_hash or access.case.invoice_content_hash
        matches = _duplicate_matches(
            store,
            tenant_id=access.tenant_id,
            case=access.case,
            invoice_number=effective_number,
            document_hash=effective_hash,
        )
        envelope = DuplicateEnvelope(
            ok=True,
            data=DuplicateView(
                case_id=request.case_id,
                invoice_number=effective_number,
                document_hash=effective_hash,
                duplicate=bool(matches),
                matches=matches,
            ),
        )
        _audit_success(
            store,
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            tool="check_duplicate",
            event="read",
            principal=context.principal,
            request=request,
            envelope=envelope,
        )
        return envelope

    @mcp.tool(
        name="record_extraction",
        description=(
            "Record extraction fields with page/bbox citations for one case document. "
            "This updates case state only and never writes a CRM decision."
        ),
        auth=require_scopes(WRITE_SCOPE),
        output_schema=ExtractionEnvelope.model_json_schema(),
        annotations=_write_annotations("Record extraction"),
    )
    async def record_extraction(
        case_id: Annotated[str, Field(min_length=1)],
        document_id: Annotated[str, Field(min_length=1)],
        fields: Annotated[list[ExtractedField], Field(min_length=1)],
    ) -> ExtractionEnvelope | ToolResult:
        context = _request_context()
        request = RecordExtractionInput(case_id=case_id, document_id=document_id, fields=fields)
        access = _access_case(store, context, request.case_id)
        if access.case is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="record_extraction",
                event="write_denied",
                principal=context.principal,
                error_code=access.error_code or "case_not_found",
                message=access.message or "Case not found.",
                request=request,
            )
            return _failure(
                ExtractionEnvelope, access.error_code or "case_not_found", access.message or ""
            )
        document = store.get_document(access.tenant_id, request.case_id, request.document_id)
        if document is None:
            message = f"Document {request.document_id!r} is not part of this case."
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="record_extraction",
                event="write_denied",
                principal=context.principal,
                error_code="document_not_found",
                message=message,
                request=request,
            )
            return _failure(ExtractionEnvelope, "document_not_found", message)
        for field in request.fields:
            citation_error = _validate_citation(request.document_id, field, document)
            if citation_error:
                _audit_failure(
                    store,
                    tenant_id=access.audit_tenant,
                    case_id=request.case_id,
                    tool="record_extraction",
                    event="write_denied",
                    principal=context.principal,
                    error_code="invalid_citation",
                    message=citation_error,
                    request=request,
                )
                return _failure(ExtractionEnvelope, "invalid_citation", citation_error)
        extraction = store.add_extraction(
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            document_id=request.document_id,
            fields=request.fields,
            principal=context.principal,
        )
        envelope = ExtractionEnvelope(ok=True, data=extraction)
        _audit_success(
            store,
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            tool="record_extraction",
            event="write",
            principal=context.principal,
            request=request,
            envelope=envelope,
        )
        return envelope

    @mcp.tool(
        name="record_recommendation",
        description=(
            "Record the recommendation, rationale, conditions and evidence flags for a "
            "case. This updates case state only; it is not an approval."
        ),
        auth=require_scopes(WRITE_SCOPE),
        output_schema=RecommendationEnvelope.model_json_schema(),
        annotations=_write_annotations("Record recommendation"),
    )
    async def record_recommendation(
        case_id: Annotated[str, Field(min_length=1)],
        recommendation: Recommendation,
        rationale: Annotated[str, Field(min_length=1)],
        conditions: list[str],
        flags: list[FlagRecord],
    ) -> RecommendationEnvelope | ToolResult:
        context = _request_context()
        request = RecordRecommendationInput(
            case_id=case_id,
            recommendation=recommendation,
            rationale=rationale,
            conditions=conditions,
            flags=flags,
        )
        access = _access_case(store, context, request.case_id)
        if access.case is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="record_recommendation",
                event="write_denied",
                principal=context.principal,
                error_code=access.error_code or "case_not_found",
                message=access.message or "Case not found.",
                request=request,
            )
            return _failure(
                RecommendationEnvelope,
                access.error_code or "case_not_found",
                access.message or "",
            )
        evidence_error = _validate_evidence(
            store,
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            flags=request.flags,
        )
        if evidence_error:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="record_recommendation",
                event="write_denied",
                principal=context.principal,
                error_code="invalid_evidence",
                message=evidence_error,
                request=request,
            )
            return _failure(RecommendationEnvelope, "invalid_evidence", evidence_error)
        recorded = store.add_recommendation(
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            recommendation=request.recommendation,
            rationale=request.rationale,
            conditions=request.conditions,
            flags=request.flags,
            principal=context.principal,
        )
        envelope = RecommendationEnvelope(ok=True, data=recorded)
        _audit_success(
            store,
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            tool="record_recommendation",
            event="write",
            principal=context.principal,
            request=request,
            envelope=envelope,
        )
        return envelope

    @mcp.tool(
        name="create_decision",
        description=(
            "Commit one human-approved underwriting decision, CRM stage and audit event "
            "in a single idempotent transaction. Requires a console-minted approval token."
        ),
        auth=require_scopes(APPROVE_SCOPE),
        output_schema=DecisionEnvelope.model_json_schema(),
        annotations=ToolAnnotations(
            title="Create decision",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def create_decision(
        case_id: Annotated[str, Field(min_length=1)],
        decision: Decision,
        rationale: Annotated[str, Field(min_length=1)],
        conditions: list[str],
        approval_token: str,
        idempotency_key: Annotated[str, Field(min_length=1)],
    ) -> DecisionEnvelope | ToolResult:
        context = _request_context()
        request = CreateDecisionInput(
            case_id=case_id,
            decision=decision,
            rationale=rationale,
            conditions=conditions,
            approval_token=approval_token,
            idempotency_key=idempotency_key,
        )
        access = _access_case(store, context, request.case_id)
        if access.case is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="create_decision",
                event="write_denied",
                principal=context.principal,
                error_code=access.error_code or "case_not_found",
                message=access.message or "Case not found.",
                request=request,
            )
            return _failure(
                DecisionEnvelope, access.error_code or "case_not_found", access.message or ""
            )

        payload_hash = decision_payload_hash(
            case_id=request.case_id,
            decision=request.decision,
            rationale=request.rationale,
            conditions=request.conditions,
        )

        # A replay must be safe even when the original approval token has since
        # expired or been consumed, so the idempotency lookup comes first.
        existing = store.get_idempotency(
            access.tenant_id,
            context.principal,
            "create_decision",
            request.idempotency_key,
        )
        if existing is not None:
            if existing.args_hash != payload_hash:
                message = "This idempotency key was already used with different arguments."
                _audit_decision_denied(
                    store,
                    tenant_id=access.tenant_id,
                    case_id=request.case_id,
                    principal=context.principal,
                    request=request,
                    error_code="idempotency_conflict",
                    message=message,
                    event="idempotency_conflict",
                )
                return _failure(DecisionEnvelope, "idempotency_conflict", message)
            stored = DecisionRecord.model_validate(existing.result).model_copy(
                update={"replayed": True}
            )
            envelope = DecisionEnvelope(ok=True, data=stored)
            _audit_decision_replay(
                store,
                tenant_id=access.tenant_id,
                case_id=request.case_id,
                principal=context.principal,
                request=request,
                envelope=envelope,
            )
            return envelope

        if not request.approval_token.strip():
            message = "A console-minted approval token is required for create_decision."
            _audit_decision_denied(
                store,
                tenant_id=access.tenant_id,
                case_id=request.case_id,
                principal=context.principal,
                request=request,
                error_code=REQUIRED_CODE,
                message=message,
            )
            return _failure(DecisionEnvelope, REQUIRED_CODE, message)

        try:
            claims = verify_approval_token(
                request.approval_token,
                secret=approval_secret,
                audience=approval_audience,
                issuer=approval_issuer,
                tenant_id=access.tenant_id,
                case_id=request.case_id,
                decision=request.decision,
                payload_hash=payload_hash,
            )
        except ApprovalTokenError as exc:
            _audit_decision_denied(
                store,
                tenant_id=access.tenant_id,
                case_id=request.case_id,
                principal=context.principal,
                request=request,
                error_code=exc.code,
                message=exc.message,
            )
            return _failure(DecisionEnvelope, exc.code, exc.message)

        if store.approval_is_used(access.tenant_id, claims.jti):
            message = "This approval token has already been used."
            _audit_decision_denied(
                store,
                tenant_id=access.tenant_id,
                case_id=request.case_id,
                principal=context.principal,
                request=request,
                error_code=REUSED_CODE,
                message=message,
            )
            return _failure(DecisionEnvelope, REUSED_CODE, message)

        try:
            record = store.commit_decision(
                tenant_id=access.tenant_id,
                case_id=request.case_id,
                decision=request.decision,
                rationale=request.rationale,
                conditions=request.conditions,
                principal=context.principal,
                approver=claims.sub,
                approval_jti=claims.jti,
                approval_expires_at=datetime.fromtimestamp(claims.exp, UTC).isoformat(
                    timespec="seconds"
                ),
                payload_hash=payload_hash,
                idempotency_key=request.idempotency_key,
                input_hash=_hash(request),
            )
        except IdempotencyConflictError:
            message = "This idempotency key was already used with different arguments."
            _audit_decision_denied(
                store,
                tenant_id=access.tenant_id,
                case_id=request.case_id,
                principal=context.principal,
                request=request,
                error_code="idempotency_conflict",
                message=message,
                event="idempotency_conflict",
            )
            return _failure(DecisionEnvelope, "idempotency_conflict", message)
        except ApprovalReusedError:
            message = "This approval token has already been used."
            _audit_decision_denied(
                store,
                tenant_id=access.tenant_id,
                case_id=request.case_id,
                principal=context.principal,
                request=request,
                error_code=REUSED_CODE,
                message=message,
            )
            return _failure(DecisionEnvelope, REUSED_CODE, message)

        envelope = DecisionEnvelope(ok=True, data=record)
        return envelope

    @mcp.tool(
        name="get_audit",
        description="Read the append-only audit events recorded for a case.",
        auth=require_scopes(READ_SCOPE),
        output_schema=AuditEnvelope.model_json_schema(),
        annotations=_read_annotations("Get audit"),
    )
    async def get_audit(
        case_id: Annotated[str, Field(min_length=1)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> AuditEnvelope | ToolResult:
        context = _request_context()
        request = GetAuditInput(case_id=case_id, limit=limit)
        access = _access_case(store, context, request.case_id)
        if access.case is None:
            _audit_failure(
                store,
                tenant_id=access.audit_tenant,
                case_id=request.case_id,
                tool="get_audit",
                event="read_denied",
                principal=context.principal,
                error_code=access.error_code or "case_not_found",
                message=access.message or "Case not found.",
                request=request,
            )
            return _failure(
                AuditEnvelope, access.error_code or "case_not_found", access.message or ""
            )
        events = store.list_audit(access.tenant_id, request.case_id, limit=request.limit)
        envelope = AuditEnvelope(
            ok=True,
            data=AuditView(case_id=request.case_id, events=events),
        )
        _audit_success(
            store,
            tenant_id=access.tenant_id,
            case_id=request.case_id,
            tool="get_audit",
            event="read",
            principal=context.principal,
            request=request,
            envelope=envelope,
        )
        return envelope
