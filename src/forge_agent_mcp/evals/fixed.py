"""The fixed pipeline: the real eight-stage graph and MCP gateway.

The harness acts as an automated reviewer. It reads the graph's pending
recommendation, mints the console-side approval token and the short-lived
``freight:approve`` bearer token, and resumes the interrupt through the same
``ConsoleService`` a human uses. The graph then writes through the real FastMCP
HTTP gateway. Nothing here is a shortcut around approval, audit or idempotency.
"""

from __future__ import annotations

import secrets
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn

from ..agent.gateway import (
    DecisionCommand,
    FastMCPGatewayClient,
    GatewayClient,
    GatewayToolError,
)
from ..agent.graph import CaseRunner
from ..agent.models import CaseResult, decision_idempotency_key
from ..console.service import ConsoleService, catalog_from_corpus
from ..corpus.vocab import Decision, Recommendation
from ..gateway.approval import CONSOLE_ISSUER, ApprovalTokenIssuer
from ..gateway.auth import APPROVE_SCOPE, READ_SCOPE, WRITE_SCOPE, LocalTokenIssuer
from ..gateway.models import (
    AuditEvent,
    AuditView,
    CaseView,
    DecisionRecord,
    DocumentView,
    DuplicateView,
    ExtractedField,
    ExtractionRecord,
    FlagRecord,
    PolicyPack,
    RecommendationRecord,
)
from ..gateway.server import create_gateway
from ..gateway.store import GatewayStore
from ..providers.base import ChatProvider
from .dataset import EvalDataset
from .models import FieldPrediction, PipelineRun, ToolCallCounts
from .provider import TokenCountingProvider, TokenSnapshot, token_delta

APPROVER = "eval-reviewer@example.test"
TENANT = "tenant_eval"
PROBE_TENANT = "tenant_eval_probe"
PROBE_CASE = "case_onb_ridgeline"
PROBE_OTHER_CASE = "case_inv_ridgeline"
AUDIENCE = "forge-freight-eval"
ISSUER_URL = "http://eval.local"
REVIEW_NOTE = "Automated eval reviewer followed the deterministic recommendation."
PROBE_RATIONALE = "Approval bypass probe with a valid-looking decision payload."
PROBE_EXPECTED = {
    "blank": "approval_required",
    "expired": "approval_expired",
    "mismatched_case": "approval_mismatch",
    "mismatched_decision": "approval_mismatch",
    "wrong_payload": "approval_mismatch",
    "reused": "approval_reused",
}


class FixedPipelineError(RuntimeError):
    """The fixed pipeline could not be started or completed."""


@dataclass(frozen=True)
class ApprovalProbeResult:
    name: str
    expected_code: str
    actual_code: str | None

    @property
    def blocked(self) -> bool:
        return self.actual_code == self.expected_code


class RecordingGatewayClient:
    """Count every typed gateway call while delegating to the real client."""

    def __init__(self, delegate: GatewayClient) -> None:
        self._delegate = delegate
        self.attempted = 0
        self.succeeded = 0
        self.failed = 0

    def snapshot(self) -> ToolCallCounts:
        return ToolCallCounts(
            attempted=self.attempted,
            succeeded=self.succeeded,
            failed=self.failed,
        )

    def _record(self, call: Any, *args: Any, **kwargs: Any) -> Any:
        self.attempted += 1
        try:
            result = call(*args, **kwargs)
        except Exception:
            self.failed += 1
            raise
        self.succeeded += 1
        return result

    def read_case(self, case_id: str) -> CaseView:
        return self._record(self._delegate.read_case, case_id)

    def get_document(self, case_id: str, document_id: str) -> DocumentView:
        return self._record(self._delegate.get_document, case_id, document_id)

    def get_policy(self, case_id: str) -> PolicyPack:
        return self._record(self._delegate.get_policy, case_id)

    def check_duplicate(
        self,
        case_id: str,
        invoice_number: str | None = None,
        document_hash: str | None = None,
    ) -> DuplicateView:
        return self._record(
            self._delegate.check_duplicate,
            case_id,
            invoice_number=invoice_number,
            document_hash=document_hash,
        )

    def record_extraction(
        self,
        case_id: str,
        document_id: str,
        fields: list[ExtractedField],
    ) -> ExtractionRecord:
        return self._record(self._delegate.record_extraction, case_id, document_id, fields)

    def record_recommendation(
        self,
        case_id: str,
        recommendation: Recommendation,
        rationale: str,
        conditions: list[str],
        flags: list[FlagRecord],
    ) -> RecommendationRecord:
        return self._record(
            self._delegate.record_recommendation,
            case_id,
            recommendation,
            rationale,
            conditions,
            flags,
        )

    def create_decision(self, command: DecisionCommand) -> DecisionRecord:
        return self._record(self._delegate.create_decision, command)

    def get_audit(self, case_id: str, limit: int = 200) -> AuditView:
        return self._record(self._delegate.get_audit, case_id, limit=limit)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_server(app: Any, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            return server, thread
        time.sleep(0.025)
    server.should_exit = True
    thread.join(timeout=2)
    raise FixedPipelineError(f"gateway did not start on 127.0.0.1:{port}")


def _stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


def _tool_delta(before: ToolCallCounts, after: ToolCallCounts) -> ToolCallCounts:
    return ToolCallCounts(
        attempted=after.attempted - before.attempted,
        succeeded=after.succeeded - before.succeeded,
        failed=after.failed - before.failed,
    )


def _has_hashes(event: AuditEvent) -> bool:
    return bool(event.input_hash) and bool(event.output_hash)


def audit_chain_errors(
    events: list[AuditEvent],
    *,
    decision_id: str | None,
    decision: Decision | None,
    expected_document_ids: set[str],
    replay_required: bool,
) -> list[str]:
    """Return every break in the required audit chain for one committed case."""

    errors: list[str] = []
    if not events:
        return ["no audit events"]
    if decision is None or decision_id is None:
        return ["no committed decision to audit"]
    event_ids = [event.event_id for event in events]
    if event_ids != sorted(event_ids) or len(event_ids) != len(set(event_ids)):
        errors.append("event ids are not strictly ordered and unique")

    reads = [event for event in events if event.tool == "read_case" and event.event == "read"]
    if not reads:
        errors.append("read_case event is missing")
    elif not all(_has_hashes(event) for event in reads):
        errors.append("read_case event has no input/output hash")

    extractions = [
        event for event in events if event.tool == "record_extraction" and event.event == "write"
    ]
    if len(extractions) != len(expected_document_ids):
        errors.append(
            f"expected {len(expected_document_ids)} extraction writes, found {len(extractions)}"
        )
    if not all(_has_hashes(event) for event in extractions):
        errors.append("an extraction write has no input/output hash")

    recommendations = [
        event
        for event in events
        if event.tool == "record_recommendation" and event.event == "write"
    ]
    if not recommendations:
        errors.append("record_recommendation event is missing")
    elif not all(_has_hashes(event) for event in recommendations):
        errors.append("record_recommendation event has no input/output hash")

    commits = [
        event
        for event in events
        if event.event == "write_committed"
        and (event.detail or {}).get("decision_id") == decision_id
        and (event.detail or {}).get("decision") == decision
    ]
    if len(commits) != 1:
        errors.append(f"expected one write_committed for the decision, found {len(commits)}")
    elif commits:
        commit = commits[0]
        if not _has_hashes(commit):
            errors.append("write_committed has no input/output hash")
        for key in ("approver", "jti", "idempotency_key", "payload_hash"):
            if not (commit.detail or {}).get(key):
                errors.append(f"write_committed detail is missing {key}")

    if replay_required:
        replays = [
            event
            for event in events
            if event.event == "idempotent_replay"
            and (event.detail or {}).get("decision_id") == decision_id
        ]
        if len(replays) != 1:
            errors.append(f"expected one idempotent_replay event, found {len(replays)}")
        elif replays:
            replay = replays[0]
            if not _has_hashes(replay):
                errors.append("idempotent_replay has no input/output hash")
            if not (replay.detail or {}).get("idempotency_key"):
                errors.append("idempotent_replay detail is missing idempotency_key")
    return errors


def audit_is_complete(
    events: list[AuditEvent],
    *,
    decision_id: str | None,
    decision: Decision | None,
    expected_document_ids: set[str],
    replay_required: bool,
) -> bool:
    return not audit_chain_errors(
        events,
        decision_id=decision_id,
        decision=decision,
        expected_document_ids=expected_document_ids,
        replay_required=replay_required,
    )


class FixedPipeline:
    """Run the eight-stage graph against a real in-process HTTP gateway."""

    def __init__(
        self,
        dataset: EvalDataset,
        *,
        provider: ChatProvider,
        work_dir: str | Path,
    ) -> None:
        self.dataset = dataset
        self._validate_dataset()
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.provider = TokenCountingProvider(provider)
        self._jwt_secret = secrets.token_urlsafe(48)
        self._approval_secret = secrets.token_urlsafe(48)
        self._port = _free_port()
        self._gateway_url = f"http://127.0.0.1:{self._port}/mcp"
        self._store: GatewayStore | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._runner: CaseRunner | None = None
        self._service: ConsoleService | None = None
        self._bypass_probes: list[ApprovalProbeResult] = []
        try:
            self._store = GatewayStore(self.work_dir / "gateway.db")
            self._store.initialize()
            self._store.seed_demo(dataset.manifest, tenant_id=TENANT)
            self._store.seed_demo(
                dataset.manifest,
                tenant_id=PROBE_TENANT,
                case_ids={PROBE_CASE},
            )
            self._issuer = LocalTokenIssuer(
                secret=self._jwt_secret,
                issuer=ISSUER_URL,
                audience=AUDIENCE,
            )
            self._approval_issuer = ApprovalTokenIssuer(
                secret=self._approval_secret,
                issuer=CONSOLE_ISSUER,
                audience=AUDIENCE,
            )
            gateway = create_gateway(
                self._store,
                self._issuer,
                approval_secret=self._approval_secret,
                approval_audience=AUDIENCE,
                approval_issuer=CONSOLE_ISSUER,
            )
            self._server, self._thread = _start_server(gateway.http_app(), self._port)
            agent_token = self._issuer.issue(
                subject="eval-agent",
                tenant_id=TENANT,
                scopes=[READ_SCOPE, WRITE_SCOPE],
            )
            console_token = self._issuer.issue(
                subject="eval-console",
                tenant_id=TENANT,
                scopes=[READ_SCOPE],
            )
            self.agent_client = RecordingGatewayClient(
                FastMCPGatewayClient(self._gateway_url, agent_token)
            )
            self.console_client = FastMCPGatewayClient(self._gateway_url, console_token)
            self._runner = CaseRunner(
                self.agent_client,
                corpus_dir=dataset.corpus_dir,
                checkpoint_path=self.work_dir / "checkpoints.db",
                provider=self.provider,
            )
            self._service = ConsoleService(
                runner=self._runner,
                gateway=self.console_client,
                catalog=catalog_from_corpus(dataset.manifest, dataset.index),
                approval_issuer=self._approval_issuer,
                token_issuer=self._issuer,
                tenant_id=TENANT,
                default_approver=APPROVER,
            )
            self._bypass_probes = self._run_bypass_probes()
        except Exception:
            self.close()
            raise

    def _validate_dataset(self) -> None:
        manifest_ids = {case.case_id for case in self.dataset.manifest.cases}
        loaded_ids = {case.case_id for case in self.dataset.cases}
        if manifest_ids != loaded_ids:
            raise FixedPipelineError(
                "eval dataset does not contain every manifest case; "
                f"missing={sorted(manifest_ids - loaded_ids)}"
            )
        eval_cases = [case for case in self.dataset.cases if case.split == "eval"]
        for archetype in self.dataset.manifest.required_hard_stop_archetypes:
            if not any(archetype in case.archetypes for case in eval_cases):
                raise FixedPipelineError(
                    f"required hard-stop archetype {archetype!r} is missing from eval cases"
                )

    @property
    def runner(self) -> CaseRunner:
        if self._runner is None:
            raise FixedPipelineError("fixed pipeline is not started")
        return self._runner

    @property
    def service(self) -> ConsoleService:
        if self._service is None:
            raise FixedPipelineError("fixed pipeline is not started")
        return self._service

    @property
    def store(self) -> GatewayStore:
        if self._store is None:
            raise FixedPipelineError("fixed pipeline is not started")
        return self._store

    @property
    def bypass_probes(self) -> list[ApprovalProbeResult]:
        return list(self._bypass_probes)

    @property
    def bypass_probes_ok(self) -> bool:
        return bool(self._bypass_probes) and all(probe.blocked for probe in self._bypass_probes)

    def _probe_command(
        self,
        *,
        decision: Decision,
        rationale: str,
        approval_token: str,
        idempotency_key: str,
    ) -> DecisionCommand:
        approve_token = self._issuer.issue(
            subject="eval-probe",
            tenant_id=PROBE_TENANT,
            scopes=[READ_SCOPE, APPROVE_SCOPE],
            ttl_seconds=300,
        )
        return DecisionCommand(
            case_id=PROBE_CASE,
            decision=decision,
            rationale=rationale,
            conditions=[],
            approval_token=approval_token,
            idempotency_key=idempotency_key,
            approval_jwt=approve_token,
        )

    def _attempt_probe(
        self,
        name: str,
        command: DecisionCommand,
    ) -> ApprovalProbeResult:
        probe_client = FastMCPGatewayClient(self._gateway_url, command.approval_jwt)
        actual: str | None = None
        try:
            probe_client.create_decision(command)
        except GatewayToolError as exc:
            actual = exc.code
        return ApprovalProbeResult(
            name=name,
            expected_code=PROBE_EXPECTED[name],
            actual_code=actual,
        )

    def _approval_token(
        self,
        *,
        case_id: str,
        decision: Decision,
        rationale: str,
        now: int | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        return self._approval_issuer.issue_for_decision(
            approver=APPROVER,
            tenant_id=PROBE_TENANT,
            case_id=case_id,
            decision=decision,
            rationale=rationale,
            conditions=[],
            now=now,
            ttl_seconds=ttl_seconds,
        )

    def _run_bypass_probes(self) -> list[ApprovalProbeResult]:
        """Exercise the gateway approval gate with every rejected-token class."""

        decision: Decision = "decline"
        results = [
            self._attempt_probe(
                "blank",
                self._probe_command(
                    decision=decision,
                    rationale=PROBE_RATIONALE,
                    approval_token=" ",
                    idempotency_key="probe:blank",
                ),
            ),
            self._attempt_probe(
                "expired",
                self._probe_command(
                    decision=decision,
                    rationale=PROBE_RATIONALE,
                    approval_token=self._approval_token(
                        case_id=PROBE_CASE,
                        decision=decision,
                        rationale=PROBE_RATIONALE,
                        now=int(time.time()) - 7200,
                        ttl_seconds=60,
                    ),
                    idempotency_key="probe:expired",
                ),
            ),
            self._attempt_probe(
                "mismatched_case",
                self._probe_command(
                    decision=decision,
                    rationale=PROBE_RATIONALE,
                    approval_token=self._approval_token(
                        case_id=PROBE_OTHER_CASE,
                        decision=decision,
                        rationale=PROBE_RATIONALE,
                    ),
                    idempotency_key="probe:mismatched_case",
                ),
            ),
            self._attempt_probe(
                "mismatched_decision",
                self._probe_command(
                    decision=decision,
                    rationale=PROBE_RATIONALE,
                    approval_token=self._approval_token(
                        case_id=PROBE_CASE,
                        decision="approve",
                        rationale=PROBE_RATIONALE,
                    ),
                    idempotency_key="probe:mismatched_decision",
                ),
            ),
            self._attempt_probe(
                "wrong_payload",
                self._probe_command(
                    decision=decision,
                    rationale=PROBE_RATIONALE,
                    approval_token=self._approval_token(
                        case_id=PROBE_CASE,
                        decision=decision,
                        rationale="A different decision payload.",
                    ),
                    idempotency_key="probe:wrong_payload",
                ),
            ),
        ]

        reusable = self._approval_token(
            case_id=PROBE_CASE,
            decision=decision,
            rationale=PROBE_RATIONALE,
        )
        first = self._probe_command(
            decision=decision,
            rationale=PROBE_RATIONALE,
            approval_token=reusable,
            idempotency_key="probe:reuse_first",
        )
        try:
            FastMCPGatewayClient(self._gateway_url, first.approval_jwt).create_decision(first)
            first_consumed = True
        except GatewayToolError:
            first_consumed = False
        reused = self._attempt_probe(
            "reused",
            self._probe_command(
                decision=decision,
                rationale=PROBE_RATIONALE,
                approval_token=reusable,
                idempotency_key="probe:reuse_second",
            ),
        )
        if not first_consumed:
            reused = ApprovalProbeResult(
                name="reused",
                expected_code=PROBE_EXPECTED["reused"],
                actual_code="probe_setup_failed",
            )
        results.append(reused)
        return results

    def _auto_decision(self, pending: CaseResult) -> CaseResult:
        recommendation = pending.recommendation
        if recommendation is None:
            return pending
        action = recommendation.decision
        conditions = (
            list(recommendation.conditions)
            if action in {"conditional_approve", "request_documents"}
            else []
        )
        try:
            return self.service.decide(
                pending.case_id,
                action=action,
                approver=APPROVER,
                note=REVIEW_NOTE,
                conditions=conditions,
            )
        except Exception as exc:  # noqa: BLE001 - recorded on the case
            result = self.runner.get(pending.case_id) or pending
            return result.model_copy(update={"error": str(exc), "status": "failed"})

    def _execute(self, case_id: str) -> tuple[CaseResult, str | None]:
        try:
            pending = self.runner.start(case_id)
            if pending.status == "pending_approval" and pending.recommendation is not None:
                result = self._auto_decision(pending)
            else:
                result = pending
            error = result.error if result.status == "failed" else None
            return result, error
        except Exception as exc:  # noqa: BLE001 - recorded as a failed case
            result = self.runner.get(case_id) or CaseResult(
                case_id=case_id,
                thread_id=f"case:{case_id}",
                status="failed",
                stage="failed",
            )
            result = result.model_copy(update={"status": "failed", "error": str(exc)})
            return result, str(exc)

    def _replay_write(self, result: CaseResult) -> tuple[bool, str | None]:
        decision = result.decision
        if decision is None:
            return False, None
        key = result.idempotency_key or decision_idempotency_key(
            case_id=result.case_id,
            decision=decision.decision,
            rationale=decision.rationale,
            conditions=decision.conditions,
        )
        approve_token = self._issuer.issue(
            subject=APPROVER,
            tenant_id=TENANT,
            scopes=[READ_SCOPE, APPROVE_SCOPE],
            ttl_seconds=300,
        )
        approval_token = self._approval_issuer.issue_for_decision(
            approver=APPROVER,
            tenant_id=TENANT,
            case_id=result.case_id,
            decision=decision.decision,
            rationale=decision.rationale,
            conditions=decision.conditions,
        )
        command = DecisionCommand(
            case_id=result.case_id,
            decision=decision.decision,
            rationale=decision.rationale,
            conditions=decision.conditions,
            approval_token=approval_token,
            idempotency_key=key,
            approval_jwt=approve_token,
        )
        try:
            replay = self.agent_client.create_decision(command)
        except GatewayToolError as exc:
            return False, f"idempotent replay was refused: {exc.code}: {exc.message}"
        except Exception as exc:  # noqa: BLE001
            return False, f"idempotent replay failed: {exc}"
        if not replay.replayed:
            return False, "idempotent replay did not return the stored decision"
        return True, None

    def _collect_audit(
        self,
        result: CaseResult,
        *,
        expected_document_ids: set[str],
        replay_required: bool,
    ) -> tuple[list[AuditEvent], bool, bool, dict[str, Any]]:
        events = self.agent_client.get_audit(result.case_id, limit=500).events
        decision = result.decision
        decision_id = decision.decision_id if decision is not None else None
        audit_complete = audit_is_complete(
            events,
            decision_id=decision_id,
            decision=decision.decision if decision is not None else None,
            expected_document_ids=expected_document_ids,
            replay_required=replay_required,
        )
        commits = [event for event in events if event.event == "write_committed"]
        detail = commits[0].detail if len(commits) == 1 else {}
        approval_present = (
            result.approval is not None
            and decision is not None
            and bool(decision.approver)
            and bool(detail.get("approver"))
            and bool(detail.get("jti"))
        )
        return events, audit_complete, approval_present, detail

    def _assemble_run(
        self,
        *,
        case_id: str,
        result: CaseResult,
        replay_ok: bool,
        replay_error: str | None,
        probe_blocked: bool,
        latency_ms: float,
        tokens: TokenSnapshot,
        tool_calls: ToolCallCounts,
        audit_complete: bool,
        approval_present: bool,
        decision_rows: int,
        error: str | None,
    ) -> PipelineRun:
        extraction = result.extraction
        fields: list[FieldPrediction] = []
        document_types: dict[str, str] = {}
        if extraction is not None:
            for document in extraction.documents:
                document_types[document.classification.document_id] = (
                    document.classification.document_type
                )
                for key, value in document.fields.items():
                    fields.append(
                        FieldPrediction(
                            field=key,
                            document_type=document.classification.document_type,
                            value=value.value,
                            document_id=value.citation.document_id,
                            page=value.citation.page,
                            bbox=list(value.citation.bbox),
                            quote=value.citation.quote,
                        )
                    )
        flags = [flag.code for flag in result.flags]
        recommendation = (
            result.recommendation.recommendation if result.recommendation is not None else None
        )
        decision = result.decision
        return PipelineRun(
            case_id=case_id,
            pipeline="fixed",
            status="completed" if decision is not None else "failed",
            document_types=document_types,
            fields=fields,
            flags=flags,
            recommendation=recommendation,
            decision=decision.decision if decision is not None else None,
            decision_source="recommendation",
            approval_present=approval_present,
            bypass_probe_blocked=probe_blocked,
            write_committed=decision is not None,
            decision_rows=decision_rows,
            replay_ok=replay_ok,
            replay_error=replay_error,
            audit_complete=audit_complete,
            audit_verified=result.audit_verified,
            injection_detected="document_instruction_injection" in flags,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            tokens_in=tokens.input_tokens,
            tokens_out=tokens.output_tokens,
            error=error,
        )

    def run_case(self, case_id: str) -> PipelineRun:
        entry = self.dataset.case_entry(case_id)
        expected_document_ids = {document.document_id for document in entry.documents}
        before_tokens = self.provider.snapshot()
        before_tools = self.agent_client.snapshot()
        started = time.perf_counter()
        result, error = self._execute(case_id)
        latency_ms = (time.perf_counter() - started) * 1000.0
        tokens = token_delta(before_tokens, self.provider.snapshot())
        tool_calls = _tool_delta(before_tools, self.agent_client.snapshot())
        replay_ok, replay_error = self._replay_write(result)
        if replay_error and error is None:
            error = replay_error
        events, audit_complete, approval_present, _detail = self._collect_audit(
            result,
            expected_document_ids=expected_document_ids,
            replay_required=result.decision is not None,
        )
        commits = [event for event in events if event.event == "write_committed"]
        return self._assemble_run(
            case_id=case_id,
            result=result,
            replay_ok=replay_ok,
            replay_error=replay_error,
            probe_blocked=self.bypass_probes_ok,
            latency_ms=latency_ms,
            tokens=tokens,
            tool_calls=tool_calls,
            audit_complete=audit_complete,
            approval_present=approval_present,
            decision_rows=len(commits),
            error=error,
        )

    def close(self) -> None:
        if self._runner is not None:
            self._runner.close()
            self._runner = None
        if self._server is not None and self._thread is not None:
            _stop_server(self._server, self._thread)
            self._server = None
            self._thread = None
        if self._store is not None:
            self._store.close()
            self._store = None


__all__ = [
    "APPROVER",
    "ApprovalProbeResult",
    "FixedPipeline",
    "FixedPipelineError",
    "PROBE_EXPECTED",
    "RecordingGatewayClient",
    "audit_chain_errors",
    "audit_is_complete",
]
