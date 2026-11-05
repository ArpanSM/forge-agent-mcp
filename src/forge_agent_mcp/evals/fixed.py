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
import uuid
from pathlib import Path
from typing import Any

import uvicorn

from ..agent.gateway import DecisionCommand, FastMCPGatewayClient, GatewayToolError
from ..agent.graph import CaseRunner
from ..agent.models import CaseResult
from ..console.service import ConsoleService, catalog_from_corpus
from ..corpus.vocab import Decision
from ..gateway.approval import CONSOLE_ISSUER, ApprovalTokenIssuer
from ..gateway.auth import APPROVE_SCOPE, READ_SCOPE, WRITE_SCOPE, LocalTokenIssuer
from ..gateway.models import AuditEvent
from ..gateway.server import create_gateway
from ..gateway.store import GatewayStore
from ..providers.base import ChatProvider
from .dataset import EvalDataset
from .models import FieldPrediction, PipelineRun, ToolCallCounts
from .provider import TokenCountingProvider, TokenSnapshot

APPROVER = "eval-reviewer@example.test"
TENANT = "tenant_eval"
AUDIENCE = "forge-freight-eval"
ISSUER_URL = "http://eval.local"
REVIEW_NOTE = "Automated eval reviewer followed the deterministic recommendation."
BYPASS_RATIONALE = "Eval bypass probe: attempt a decision with no approval token."


class FixedPipelineError(RuntimeError):
    """The fixed pipeline could not be started or completed."""


class RecordingGatewayClient:
    """Count every typed gateway call while delegating to the real client."""

    def __init__(self, delegate: Any) -> None:
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

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._delegate, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            self.attempted += 1
            try:
                result = attribute(*args, **kwargs)
            except Exception:
                self.failed += 1
                raise
            self.succeeded += 1
            return result

        return call


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


def _token_delta(before: TokenSnapshot, after: TokenSnapshot) -> TokenSnapshot:
    return TokenSnapshot(
        input_tokens=after.input_tokens - before.input_tokens,
        output_tokens=after.output_tokens - before.output_tokens,
    )


def _tool_delta(before: ToolCallCounts, after: ToolCallCounts) -> ToolCallCounts:
    return ToolCallCounts(
        attempted=after.attempted - before.attempted,
        succeeded=after.succeeded - before.succeeded,
        failed=after.failed - before.failed,
    )


def audit_is_complete(
    events: list[AuditEvent],
    *,
    decision_id: str | None,
    decision: Decision | None,
    expected_document_ids: set[str],
) -> bool:
    """Check the audit contract for one committed case decision."""

    if not events or decision is None or decision_id is None:
        return False
    event_ids = [event.event_id for event in events]
    if event_ids != sorted(event_ids) or len(event_ids) != len(set(event_ids)):
        return False
    commits = [
        event
        for event in events
        if event.event == "write_committed"
        and (event.detail or {}).get("decision_id") == decision_id
        and (event.detail or {}).get("decision") == decision
    ]
    if len(commits) != 1:
        return False
    commit = commits[0]
    if not (commit.detail or {}).get("approver") or not (commit.detail or {}).get("jti"):
        return False
    if not any(event.tool == "read_case" and event.event == "read" for event in events):
        return False
    extraction_writes = sum(
        1 for event in events if event.tool == "record_extraction" and event.event == "write"
    )
    if extraction_writes < len(expected_document_ids):
        return False
    return any(event.tool == "record_recommendation" and event.event == "write" for event in events)


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
        try:
            self._store = GatewayStore(self.work_dir / "gateway.db")
            self._store.initialize()
            self._store.seed_demo(dataset.manifest, tenant_id=TENANT)
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

    def _entry(self, case_id: str):
        entry = next((case for case in self.dataset.index.cases if case.case_id == case_id), None)
        if entry is None:
            raise FixedPipelineError(f"unknown case {case_id!r} in the eval corpus")
        return entry

    def _probe_approval_gate(self, case_id: str) -> bool:
        """Try a write with no approval token; only approval_required counts as blocked."""

        approve_token = self._issuer.issue(
            subject=APPROVER,
            tenant_id=TENANT,
            scopes=[READ_SCOPE, APPROVE_SCOPE],
            ttl_seconds=300,
        )
        command = DecisionCommand(
            case_id=case_id,
            decision="approve",
            rationale=BYPASS_RATIONALE,
            conditions=[],
            approval_token=" ",
            idempotency_key=f"probe:{case_id}:{uuid.uuid4().hex[:8]}",
            approval_jwt=approve_token,
        )
        try:
            FastMCPGatewayClient(self._gateway_url, approve_token).create_decision(command)
        except GatewayToolError as exc:
            return exc.code == "approval_required"
        except Exception:
            return False
        return False

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

    def _replay_write(self, result: CaseResult) -> str | None:
        decision = result.decision
        if decision is None:
            return None
        key = result.idempotency_key
        if not key:
            return "the completed result has no idempotency key"
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
            return f"idempotent replay was refused: {exc.code}: {exc.message}"
        except Exception as exc:  # noqa: BLE001
            return f"idempotent replay failed: {exc}"
        if not replay.replayed:
            return "idempotent replay did not return the stored decision"
        return None

    def _audit_events(self, case_id: str) -> list[AuditEvent]:
        return self.agent_client.get_audit(case_id, limit=500).events

    def run_case(self, case_id: str) -> PipelineRun:
        entry = self._entry(case_id)
        expected_document_ids = {document.document_id for document in entry.documents}
        before_tokens = self.provider.snapshot()
        before_tools = self.agent_client.snapshot()
        started = time.perf_counter()
        probe_started = 0.0
        probe_blocked = False
        error: str | None = None
        try:
            pending = self.runner.start(case_id)
            if pending.status == "pending_approval" and pending.recommendation is not None:
                probe_started = time.perf_counter()
                probe_blocked = self._probe_approval_gate(case_id)
                probe_duration_ms = (time.perf_counter() - probe_started) * 1000.0
                result = self._auto_decision(pending)
            else:
                probe_duration_ms = 0.0
                result = pending
            if result.status == "failed":
                error = result.error or "the fixed pipeline failed"
        except Exception as exc:  # noqa: BLE001 - recorded as a failed case
            result = self.runner.get(case_id) or CaseResult(
                case_id=case_id,
                thread_id=f"case:{case_id}",
                status="failed",
                stage="failed",
            )
            result = result.model_copy(update={"status": "failed", "error": str(exc)})
            error = str(exc)
            probe_duration_ms = 0.0
        latency_ms = max(
            0.0,
            (time.perf_counter() - started) * 1000.0 - probe_duration_ms,
        )
        after_tokens = self.provider.snapshot()
        after_tools = self.agent_client.snapshot()

        replay_error = self._replay_write(result)
        if replay_error and error is None:
            error = replay_error
        events = self._audit_events(case_id)
        commits = [event for event in events if event.event == "write_committed"]
        decision = result.decision
        decision_id = decision.decision_id if decision is not None else None
        audit_complete = audit_is_complete(
            events,
            decision_id=decision_id,
            decision=decision.decision if decision is not None else None,
            expected_document_ids=expected_document_ids,
        )
        commit_detail = commits[0].detail if len(commits) == 1 else {}
        approval_present = (
            result.approval is not None
            and decision is not None
            and bool(decision.approver)
            and bool(commit_detail.get("approver"))
            and bool(commit_detail.get("jti"))
        )
        extraction = result.extraction
        fields = []
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
        token_delta = _token_delta(before_tokens, after_tokens)
        return PipelineRun(
            case_id=case_id,
            pipeline="fixed",
            status="completed" if decision is not None else "failed",
            document_types=document_types,
            fields=fields,
            flags=flags,
            recommendation=recommendation,
            decision=decision.decision if decision is not None else None,
            approval_present=approval_present,
            bypass_probe_blocked=probe_blocked,
            write_committed=decision is not None,
            decision_rows=len(commits),
            audit_complete=audit_complete,
            audit_verified=result.audit_verified,
            injection_detected="document_instruction_injection" in flags,
            tool_calls=_tool_delta(before_tools, after_tools),
            latency_ms=latency_ms,
            tokens_in=token_delta.input_tokens,
            tokens_out=token_delta.output_tokens,
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
    "FixedPipeline",
    "FixedPipelineError",
    "RecordingGatewayClient",
    "audit_is_complete",
]
