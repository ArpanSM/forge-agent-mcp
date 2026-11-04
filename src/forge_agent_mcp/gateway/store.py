"""SQLite store for cases, case state and the append-only audit trail."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..corpus.manifest import Manifest
from ..corpus.pdf import render_pdf
from ..corpus.templates import build_documents, build_records
from ..corpus.vocab import CaseType, Decision, Recommendation
from .approval import ApprovalClaims
from .canonical import canonical_hash
from .models import (
    ApprovalRecord,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
    CarrierView,
    CaseRecord,
    CaseStatus,
    CitationView,
    CreateDecisionInput,
    CrmStage,
    CrmStageRecord,
    DecisionRecord,
    DocumentBBox,
    DocumentPage,
    DocumentRecord,
    ExtractedField,
    ExtractionRecord,
    FlagRecord,
    IdempotencyRecord,
    PolicyPack,
    RecommendationRecord,
    StoredRecord,
)
from .policies import build_policy_pack

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    case_type TEXT NOT NULL,
    carrier_id TEXT NOT NULL,
    carrier_json TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    invoice_number TEXT,
    invoice_content_hash TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, case_id)
);
CREATE INDEX IF NOT EXISTS cases_case_idx ON cases (case_id);

CREATE TABLE IF NOT EXISTS documents (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    pages INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    text TEXT NOT NULL,
    page_text_json TEXT NOT NULL,
    bboxes_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (tenant_id, case_id, document_id)
);

CREATE TABLE IF NOT EXISTS records (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, case_id, record_id)
);

CREATE TABLE IF NOT EXISTS policies (
    tenant_id TEXT NOT NULL,
    case_type TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, case_type)
);

CREATE TABLE IF NOT EXISTS extractions (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    extraction_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, extraction_id)
);

CREATE TABLE IF NOT EXISTS flags (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    flag_id TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    rule TEXT NOT NULL,
    details TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, flag_id)
);

CREATE TABLE IF NOT EXISTS recommendations (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    recommendation_id TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    rationale TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, recommendation_id)
);
CREATE INDEX IF NOT EXISTS recommendations_case_idx
    ON recommendations (tenant_id, case_id, created_at);

CREATE TABLE IF NOT EXISTS decisions (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    conditions_json TEXT NOT NULL DEFAULT '[]',
    approver TEXT,
    principal TEXT,
    approval_jti TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, decision_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    jti TEXT NOT NULL,
    approver TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, approval_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS approvals_jti_idx ON approvals (tenant_id, jti);

CREATE TABLE IF NOT EXISTS crm_stages (
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    crm_stage_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, crm_stage_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS crm_stages_decision_idx
    ON crm_stages (tenant_id, decision_id);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    case_id TEXT,
    tool TEXT NOT NULL,
    event TEXT NOT NULL,
    outcome TEXT NOT NULL,
    principal TEXT NOT NULL,
    input_hash TEXT,
    output_hash TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_case_idx ON audit (tenant_id, case_id, seq);

CREATE TABLE IF NOT EXISTS idempotency (
    tenant_id TEXT NOT NULL,
    principal TEXT NOT NULL,
    tool TEXT NOT NULL,
    key TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, principal, tool, key)
);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit is append-only');
END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class IdempotencyConflictError(Exception):
    """The same key was already used with different canonical arguments."""


class ApprovalReusedError(Exception):
    """The approval token's ``jti`` was already consumed by a committed write."""


class UnknownDecisionError(Exception):
    """The decision is outside the four values the CRM mapping understands."""


@dataclass(frozen=True)
class DecisionOutcome:
    crm_stage: CrmStage
    case_status: CaseStatus


DECISION_OUTCOMES: dict[Decision, DecisionOutcome] = {
    "approve": DecisionOutcome(crm_stage="approved", case_status="approved"),
    "conditional_approve": DecisionOutcome(
        crm_stage="conditionally_approved", case_status="approved"
    ),
    "decline": DecisionOutcome(crm_stage="declined", case_status="declined"),
    "request_documents": DecisionOutcome(
        crm_stage="awaiting_documents", case_status="awaiting_documents"
    ),
}


def decision_outcome(decision: Decision) -> DecisionOutcome:
    try:
        return DECISION_OUTCOMES[decision]
    except KeyError as exc:
        raise UnknownDecisionError(f"Unknown decision: {decision!r}") from exc


class GatewayStore:
    """Thin, typed SQLite adapter; every public read takes a tenant id."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(SCHEMA)

    # -- seeding ---------------------------------------------------------

    def seed_demo(
        self,
        manifest: Manifest,
        *,
        tenant_id: str,
        case_ids: set[str] | None = None,
    ) -> int:
        """Seed cases and document metadata from the synthetic corpus manifest.

        The seed is idempotent: an already-seeded tenant is left untouched so a
        restart never overwrites case state or audit rows. ``case_ids`` limits a
        seed to a subset, which lets tests model tenant-owned cases without a
        global case lookup.
        """

        with self._lock:
            existing = self.case_count(tenant_id)
            if existing:
                return existing
            carriers = manifest.carrier_by_id
            with self._connection:
                for case in manifest.cases:
                    if case_ids is not None and case.case_id not in case_ids:
                        continue
                    carrier = carriers[case.carrier_id]
                    carrier_view = CarrierView(
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
                    )
                    documents = build_documents(manifest, carrier, case)
                    rendered_by_type = {}
                    invoice_number = (
                        carrier.invoice.invoice_number if case.case_type == "invoice" else None
                    )
                    for spec in documents:
                        rendered = render_pdf(
                            spec,
                            factor_name=manifest.factor.name,
                            case_id=case.case_id,
                            carrier_id=carrier.carrier_id,
                        )
                        rendered_by_type[spec.document_type] = rendered
                        page_fields: dict[int, list[str]] = defaultdict(list)
                        bboxes: list[DocumentBBox] = []
                        for field in spec.fields:
                            citation = rendered.citations[field.key]
                            page_number = citation.page or 1
                            page_fields[page_number].append(f"{field.label}: {field.value}")
                            if citation.bbox is not None:
                                bboxes.append(
                                    DocumentBBox(
                                        field=field.key,
                                        document_id=spec.document_id,
                                        page=page_number,
                                        bbox=citation.bbox,
                                    )
                                )
                        page_text = [
                            DocumentPage(page=page, text="\n".join(page_fields[page]))
                            for page in sorted(page_fields)
                        ]
                        text = f"{spec.title} ({spec.document_id})"
                        for page in page_text:
                            text += f"\n\nPage {page.page}\n{page.text}"
                        self._connection.execute(
                            """
                            INSERT INTO documents (
                                tenant_id, case_id, document_id, document_type, title,
                                filename, pages, content_hash, sha256, text,
                                page_text_json, bboxes_json, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                            """,
                            (
                                tenant_id,
                                case.case_id,
                                spec.document_id,
                                spec.document_type,
                                spec.title,
                                spec.filename,
                                rendered.pages,
                                rendered.content_hash,
                                rendered.sha256,
                                text,
                                json.dumps(
                                    [page.model_dump() for page in page_text], sort_keys=True
                                ),
                                json.dumps([bbox.model_dump() for bbox in bboxes], sort_keys=True),
                            ),
                        )

                    invoice_document = rendered_by_type.get("invoice")
                    invoice_content_hash = (
                        invoice_document.content_hash if invoice_document is not None else None
                    )
                    for record in build_records(
                        manifest,
                        carrier,
                        case,
                        invoice_content_hash=invoice_content_hash,
                    ):
                        self._connection.execute(
                            """
                            INSERT INTO records (
                                tenant_id, case_id, record_id, record_type, data_json
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                tenant_id,
                                case.case_id,
                                record.record_id,
                                record.record_type,
                                json.dumps(record.data.model_dump(mode="json"), sort_keys=True),
                            ),
                        )

                    self._connection.execute(
                        """
                        INSERT INTO cases (
                            tenant_id, case_id, case_type, carrier_id, carrier_json,
                            status, summary, invoice_number, invoice_content_hash, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'pending_review', ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            case.case_id,
                            case.case_type,
                            case.carrier_id,
                            carrier_view.model_dump_json(),
                            "",
                            invoice_number,
                            invoice_content_hash,
                            _now(),
                        ),
                    )
                for case_type in ("onboarding", "invoice"):
                    policy = build_policy_pack(manifest, case_type)
                    self._connection.execute(
                        """
                        INSERT INTO policies (tenant_id, case_type, policy_json)
                        VALUES (?, ?, ?)
                        """,
                        (tenant_id, case_type, policy.model_dump_json()),
                    )
            return self.case_count(tenant_id)

    # -- case reads ------------------------------------------------------

    def case_count(self, tenant_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM cases WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        return int(row["count"])

    def case_tenants(self, case_id: str) -> list[str]:
        rows = self._connection.execute(
            "SELECT tenant_id FROM cases WHERE case_id = ? ORDER BY tenant_id",
            (case_id,),
        ).fetchall()
        return [str(row["tenant_id"]) for row in rows]

    def get_case(self, tenant_id: str, case_id: str) -> CaseRecord | None:
        row = self._connection.execute(
            """
            SELECT c.*, (
                SELECT COUNT(*) FROM documents d
                WHERE d.tenant_id = c.tenant_id AND d.case_id = c.case_id
            ) AS document_count
            FROM cases c
            WHERE c.tenant_id = ? AND c.case_id = ?
            """,
            (tenant_id, case_id),
        ).fetchone()
        if row is None:
            return None
        return CaseRecord(
            tenant_id=row["tenant_id"],
            case_id=row["case_id"],
            case_type=row["case_type"],
            carrier=CarrierView.model_validate_json(row["carrier_json"]),
            status=row["status"],
            summary=row["summary"],
            invoice_number=row["invoice_number"],
            invoice_content_hash=row["invoice_content_hash"],
            document_count=row["document_count"],
        )

    def list_cases(self, tenant_id: str) -> list[CaseRecord]:
        rows = self._connection.execute(
            "SELECT case_id FROM cases WHERE tenant_id = ? ORDER BY case_id", (tenant_id,)
        ).fetchall()
        cases = []
        for row in rows:
            case = self.get_case(tenant_id, row["case_id"])
            if case is not None:
                cases.append(case)
        return cases

    def list_documents(self, tenant_id: str, case_id: str) -> list[DocumentRecord]:
        rows = self._connection.execute(
            """
            SELECT * FROM documents
            WHERE tenant_id = ? AND case_id = ?
            ORDER BY document_type, document_id
            """,
            (tenant_id, case_id),
        ).fetchall()
        return [self._document_from_row(row) for row in rows]

    def get_document(self, tenant_id: str, case_id: str, document_id: str) -> DocumentRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM documents
            WHERE tenant_id = ? AND case_id = ? AND document_id = ?
            """,
            (tenant_id, case_id, document_id),
        ).fetchone()
        return self._document_from_row(row) if row else None

    def _document_from_row(self, row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(
            tenant_id=row["tenant_id"],
            case_id=row["case_id"],
            document_id=row["document_id"],
            document_type=row["document_type"],
            title=row["title"],
            filename=row["filename"],
            pages=row["pages"],
            content_hash=row["content_hash"],
            sha256=row["sha256"],
            text=row["text"],
            page_text=[
                DocumentPage.model_validate(item) for item in json.loads(row["page_text_json"])
            ],
            bboxes=[DocumentBBox.model_validate(item) for item in json.loads(row["bboxes_json"])],
            status=row["status"],
        )

    def list_records(
        self,
        tenant_id: str,
        *,
        case_id: str | None = None,
        record_type: str | None = None,
    ) -> list[StoredRecord]:
        clauses = ["tenant_id = ?"]
        values: list[Any] = [tenant_id]
        if case_id is not None:
            clauses.append("case_id = ?")
            values.append(case_id)
        if record_type is not None:
            clauses.append("record_type = ?")
            values.append(record_type)
        rows = self._connection.execute(
            "SELECT * FROM records WHERE " + " AND ".join(clauses) + " ORDER BY case_id, record_id",
            values,
        ).fetchall()
        return [
            StoredRecord(
                tenant_id=row["tenant_id"],
                case_id=row["case_id"],
                record_id=row["record_id"],
                record_type=row["record_type"],
                data=json.loads(row["data_json"]),
            )
            for row in rows
        ]

    def get_policy(self, tenant_id: str, case_type: CaseType) -> PolicyPack | None:
        row = self._connection.execute(
            "SELECT policy_json FROM policies WHERE tenant_id = ? AND case_type = ?",
            (tenant_id, case_type),
        ).fetchone()
        return PolicyPack.model_validate_json(row["policy_json"]) if row else None

    # -- case state writes ----------------------------------------------

    def add_extraction(
        self,
        *,
        tenant_id: str,
        case_id: str,
        document_id: str,
        fields: list[ExtractedField],
        principal: str,
    ) -> ExtractionRecord:
        record = ExtractionRecord(
            extraction_id=f"ext_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            document_id=document_id,
            fields=fields,
            recorded_by=principal,
            created_at=_now(),
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO extractions (
                    tenant_id, case_id, extraction_id, document_id, fields_json,
                    recorded_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    case_id,
                    record.extraction_id,
                    document_id,
                    json.dumps([field.model_dump() for field in fields], sort_keys=True),
                    principal,
                    record.created_at,
                ),
            )
            self._connection.execute(
                """
                UPDATE documents SET status = 'extracted'
                WHERE tenant_id = ? AND case_id = ? AND document_id = ?
                """,
                (tenant_id, case_id, document_id),
            )
            self._connection.execute(
                """
                UPDATE cases SET status = 'extracting'
                WHERE tenant_id = ? AND case_id = ? AND status = 'pending_review'
                """,
                (tenant_id, case_id),
            )
        return record

    def count_extractions(self, tenant_id: str, case_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM extractions WHERE tenant_id = ? AND case_id = ?",
            (tenant_id, case_id),
        ).fetchone()
        return int(row["count"])

    def add_recommendation(
        self,
        *,
        tenant_id: str,
        case_id: str,
        recommendation: Recommendation,
        rationale: str,
        conditions: list[str],
        flags: list[FlagRecord],
        principal: str,
    ) -> RecommendationRecord:
        record = RecommendationRecord(
            recommendation_id=f"rec_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            recommendation=recommendation,
            rationale=rationale,
            conditions=conditions,
            flags=flags,
            recorded_by=principal,
            created_at=_now(),
        )
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM flags WHERE tenant_id = ? AND case_id = ?", (tenant_id, case_id)
            )
            for index, flag in enumerate(flags):
                self._connection.execute(
                    """
                    INSERT INTO flags (
                        tenant_id, case_id, flag_id, code, severity, rule,
                        details, evidence_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        case_id,
                        f"{record.recommendation_id}_{index}",
                        flag.code,
                        flag.severity,
                        flag.rule,
                        flag.details,
                        json.dumps(flag.evidence.model_dump(), sort_keys=True),
                        record.created_at,
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO recommendations (
                    tenant_id, case_id, recommendation_id, recommendation, rationale,
                    conditions_json, flags_json, recorded_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    case_id,
                    record.recommendation_id,
                    recommendation,
                    rationale,
                    json.dumps(conditions, sort_keys=True),
                    json.dumps([flag.model_dump() for flag in flags], sort_keys=True),
                    principal,
                    record.created_at,
                ),
            )
        return record

    def latest_recommendation(self, tenant_id: str, case_id: str) -> RecommendationRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM recommendations
            WHERE tenant_id = ? AND case_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (tenant_id, case_id),
        ).fetchone()
        if row is None:
            return None
        return RecommendationRecord(
            recommendation_id=row["recommendation_id"],
            case_id=row["case_id"],
            recommendation=row["recommendation"],
            rationale=row["rationale"],
            conditions=json.loads(row["conditions_json"]),
            flags=[FlagRecord.model_validate(item) for item in json.loads(row["flags_json"])],
            recorded_by=row["recorded_by"],
            created_at=row["created_at"],
        )

    def list_flags(self, tenant_id: str, case_id: str) -> list[FlagRecord]:
        rows = self._connection.execute(
            "SELECT * FROM flags WHERE tenant_id = ? AND case_id = ? ORDER BY rowid",
            (tenant_id, case_id),
        ).fetchall()
        return [
            FlagRecord(
                code=row["code"],
                severity=row["severity"],
                rule=row["rule"],
                details=row["details"],
                evidence=CitationView.model_validate_json(row["evidence_json"]),
            )
            for row in rows
        ]

    def latest_decision(self, tenant_id: str, case_id: str) -> DecisionRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM decisions
            WHERE tenant_id = ? AND case_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (tenant_id, case_id),
        ).fetchone()
        if row is None:
            return None
        return DecisionRecord(
            decision_id=row["decision_id"],
            case_id=row["case_id"],
            decision=row["decision"],
            rationale=row["rationale"],
            conditions=json.loads(row["conditions_json"]),
            approver=row["approver"],
            principal=row["principal"],
            created_at=row["created_at"],
        )

    def latest_crm_stage(self, tenant_id: str, case_id: str) -> CrmStageRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM crm_stages
            WHERE tenant_id = ? AND case_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (tenant_id, case_id),
        ).fetchone()
        if row is None:
            return None
        return CrmStageRecord(
            crm_stage_id=row["crm_stage_id"],
            case_id=row["case_id"],
            stage=row["stage"],
            decision_id=row["decision_id"],
            created_at=row["created_at"],
        )

    def get_idempotency(
        self, tenant_id: str, principal: str, tool: str, key: str
    ) -> IdempotencyRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM idempotency
            WHERE tenant_id = ? AND principal = ? AND tool = ? AND key = ?
            """,
            (tenant_id, principal, tool, key),
        ).fetchone()
        if row is None:
            return None
        return IdempotencyRecord(
            tenant_id=row["tenant_id"],
            principal=row["principal"],
            tool=row["tool"],
            key=row["key"],
            args_hash=row["args_hash"],
            result=DecisionRecord.model_validate_json(row["result_json"]),
            created_at=row["created_at"],
        )

    def approval_is_used(self, tenant_id: str, jti: str) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM approvals
            WHERE tenant_id = ? AND jti = ? AND used_at IS NOT NULL
            """,
            (tenant_id, jti),
        ).fetchone()
        return row is not None

    def commit_decision(
        self,
        *,
        tenant_id: str,
        request: CreateDecisionInput,
        claims: ApprovalClaims,
        principal: str,
        input_hash: str,
    ) -> DecisionRecord:
        """Write decision, CRM stage, approval use, idempotency and audit atomically.

        The idempotency and single-use checks happen inside the same transaction as
        the writes, so a retry cannot create a second decision. A same-key retry with
        the same canonical arguments returns the stored result with ``replayed`` set;
        different arguments raise ``IdempotencyConflictError``.
        """

        now = _now()
        payload_hash = claims.payload_hash
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT args_hash, result_json FROM idempotency
                WHERE tenant_id = ? AND principal = ? AND tool = ? AND key = ?
                """,
                (tenant_id, principal, "create_decision", request.idempotency_key),
            ).fetchone()
            if row is not None:
                if row["args_hash"] != payload_hash:
                    raise IdempotencyConflictError(
                        "This idempotency key was already used with different arguments."
                    )
                stored = DecisionRecord.model_validate_json(row["result_json"]).model_copy(
                    update={"replayed": True}
                )
                self._insert_audit(
                    tenant_id=tenant_id,
                    case_id=request.case_id,
                    tool="create_decision",
                    event="idempotent_replay",
                    outcome="ok",
                    principal=principal,
                    input_hash=input_hash,
                    output_hash=canonical_hash(stored),
                    detail={
                        "decision_id": stored.decision_id,
                        "idempotency_key": request.idempotency_key,
                        "source": "transaction",
                    },
                )
                return stored

            used = self._connection.execute(
                """
                SELECT 1 FROM approvals
                WHERE tenant_id = ? AND jti = ? AND used_at IS NOT NULL
                """,
                (tenant_id, claims.jti),
            ).fetchone()
            if used is not None:
                raise ApprovalReusedError("This approval token has already been used.")

            outcome = decision_outcome(request.decision)
            record = DecisionRecord(
                decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                case_id=request.case_id,
                decision=request.decision,
                rationale=request.rationale,
                conditions=request.conditions,
                approver=claims.sub,
                principal=principal,
                created_at=now,
            )
            result_json = json.dumps(record.model_dump(mode="json"), sort_keys=True)
            approval_record = ApprovalRecord(
                tenant_id=tenant_id,
                approval_id=f"apr_{uuid.uuid4().hex[:12]}",
                case_id=request.case_id,
                jti=claims.jti,
                approver=claims.sub,
                payload_hash=payload_hash,
                expires_at=datetime.fromtimestamp(claims.exp, UTC).isoformat(timespec="seconds"),
                used_at=now,
                created_at=now,
            )
            idempotency_record = IdempotencyRecord(
                tenant_id=tenant_id,
                principal=principal,
                tool="create_decision",
                key=request.idempotency_key,
                args_hash=payload_hash,
                result=record,
                created_at=now,
            )

            self._connection.execute(
                """
                INSERT INTO decisions (
                    tenant_id, case_id, decision_id, decision, rationale,
                    conditions_json, approver, principal, approval_jti,
                    idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    request.case_id,
                    record.decision_id,
                    request.decision,
                    request.rationale,
                    json.dumps(request.conditions, sort_keys=True),
                    claims.sub,
                    principal,
                    claims.jti,
                    request.idempotency_key,
                    now,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO approvals (
                    tenant_id, case_id, approval_id, jti, approver,
                    payload_hash, expires_at, used_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_record.tenant_id,
                    approval_record.case_id,
                    approval_record.approval_id,
                    approval_record.jti,
                    approval_record.approver,
                    approval_record.payload_hash,
                    approval_record.expires_at,
                    approval_record.used_at,
                    approval_record.created_at,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO crm_stages (
                    tenant_id, case_id, crm_stage_id, stage, decision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    request.case_id,
                    f"crm_{uuid.uuid4().hex[:12]}",
                    outcome.crm_stage,
                    record.decision_id,
                    now,
                ),
            )
            self._connection.execute(
                """
                UPDATE cases SET status = ?
                WHERE tenant_id = ? AND case_id = ?
                """,
                (outcome.case_status, tenant_id, request.case_id),
            )
            self._connection.execute(
                """
                INSERT INTO idempotency (
                    tenant_id, principal, tool, key, args_hash, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_record.tenant_id,
                    idempotency_record.principal,
                    idempotency_record.tool,
                    idempotency_record.key,
                    idempotency_record.args_hash,
                    result_json,
                    idempotency_record.created_at,
                ),
            )
            self._insert_audit(
                tenant_id=tenant_id,
                case_id=request.case_id,
                tool="create_decision",
                event="write_committed",
                outcome="ok",
                principal=principal,
                input_hash=input_hash,
                output_hash=canonical_hash(record),
                detail={
                    "decision_id": record.decision_id,
                    "decision": request.decision,
                    "crm_stage": outcome.crm_stage,
                    "case_status": outcome.case_status,
                    "approver": claims.sub,
                    "jti": claims.jti,
                    "idempotency_key": request.idempotency_key,
                    "payload_hash": payload_hash,
                },
            )
        return record

    # -- audit -----------------------------------------------------------

    def _insert_audit(
        self,
        *,
        tenant_id: str,
        case_id: str | None,
        tool: str,
        event: AuditEventType,
        outcome: AuditOutcome,
        principal: str,
        input_hash: str | None = None,
        output_hash: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Insert one audit row without opening a transaction of its own."""

        cursor = self._connection.execute(
            """
            INSERT INTO audit (
                tenant_id, case_id, tool, event, outcome, principal,
                input_hash, output_hash, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                case_id,
                tool,
                event,
                outcome,
                principal,
                input_hash,
                output_hash,
                json.dumps(detail or {}, sort_keys=True),
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    def append_audit(
        self,
        *,
        tenant_id: str,
        case_id: str | None,
        tool: str,
        event: AuditEventType,
        outcome: AuditOutcome,
        principal: str,
        input_hash: str | None = None,
        output_hash: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> int:
        with self._lock, self._connection:
            return self._insert_audit(
                tenant_id=tenant_id,
                case_id=case_id,
                tool=tool,
                event=event,
                outcome=outcome,
                principal=principal,
                input_hash=input_hash,
                output_hash=output_hash,
                detail=detail,
            )

    def list_audit(self, tenant_id: str, case_id: str, *, limit: int = 100) -> list[AuditEvent]:
        rows = self._connection.execute(
            """
            SELECT * FROM audit
            WHERE tenant_id = ? AND case_id = ?
            ORDER BY seq
            LIMIT ?
            """,
            (tenant_id, case_id, limit),
        ).fetchall()
        return [
            AuditEvent(
                event_id=row["seq"],
                tenant_id=row["tenant_id"],
                case_id=row["case_id"],
                tool=row["tool"],
                event=row["event"],
                outcome=row["outcome"],
                principal=row["principal"],
                input_hash=row["input_hash"],
                output_hash=row["output_hash"],
                detail=json.loads(row["detail_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
