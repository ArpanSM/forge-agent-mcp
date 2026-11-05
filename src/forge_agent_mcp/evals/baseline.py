"""The deliberately naive baseline.

One model prompt over concatenated packet text. The baseline parses the
provider's JSON decision and writes it directly to a small CRM-like store. It
has no document classification, fields, citations, validators, approval or
audit, and it ignores the idempotency key so a retry duplicates the decision.
If the response is not a valid JSON decision, it records a fallback approve
instead of pretending the prompt succeeded.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from ..agent.models import decision_idempotency_key
from ..corpus.vocab import Decision, StrictModel
from ..extraction.classify import read_document_text
from ..gateway.models import CrmStage
from ..gateway.store import decision_outcome
from ..providers.base import ChatMessage, ChatProvider, ProviderError
from .dataset import EvalDataset
from .models import DecisionSource, PipelineRun, ToolCallCounts
from .provider import TokenCountingProvider, token_delta

BASELINE_DEFAULT_RESPONSE = json.dumps(
    {
        "decision": "approve",
        "reason": "Offline FakeProvider returns a deterministic approve decision.",
    }
)
BASELINE_RATIONALE = (
    "Naive baseline: approve every packet without citations, validators, "
    "cross-checks or human approval."
)
BASELINE_FALLBACK_RATIONALE = (
    "Naive baseline fallback: the provider response was not a valid JSON decision, "
    "so the baseline approved by default without validators or approval."
)
BASELINE_SYSTEM_PROMPT = (
    "You are a freight underwriting assistant. Read the concatenated packet text and "
    "recommend whether to onboard the carrier or fund the invoice. Return exactly one "
    'JSON object shaped {"decision": "approve|conditional_approve|decline|'
    'request_documents", "reason": "short explanation"}. Do not ask for approval.'
)

BASELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS baseline_decisions (
    decision_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    crm_stage TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS baseline_decisions_case_idx
    ON baseline_decisions (case_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class BaselineError(RuntimeError):
    """The baseline could not read its packet or run its direct write."""


class BaselineDecision(StrictModel):
    decision: Decision
    reason: str = Field(default="")


class BaselineWrite(StrictModel):
    case_id: str = Field(min_length=1)
    decision: Decision
    rationale: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1)
    crm_stage: CrmStage


def _strip_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def parse_baseline_decision(text: str) -> BaselineDecision | None:
    """Parse one provider response, returning ``None`` for an unusable answer."""

    payload: Any
    try:
        payload = json.loads(_strip_fence(text))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return BaselineDecision.model_validate(payload)
    except ValueError:
        return None


class BaselineStore:
    """A direct-write CRM stand-in with no approval, audit or idempotency."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        with self._connection:
            self._connection.executescript(BASELINE_SCHEMA)

    def write_decision(
        self,
        *,
        case_id: str,
        decision: Decision,
        rationale: str,
        conditions: list[str],
        idempotency_key: str,
        crm_stage: CrmStage,
    ) -> str:
        write = BaselineWrite(
            case_id=case_id,
            decision=decision,
            rationale=rationale,
            conditions=conditions,
            idempotency_key=idempotency_key,
            crm_stage=crm_stage,
        )
        decision_id = f"base_dec_{uuid.uuid4().hex[:12]}"
        with self._connection, self._connection:
            self._connection.execute(
                """
                INSERT INTO baseline_decisions (
                    decision_id, case_id, decision, rationale, conditions_json,
                    idempotency_key, crm_stage, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    write.case_id,
                    write.decision,
                    write.rationale,
                    json.dumps(write.conditions),
                    write.idempotency_key,
                    write.crm_stage,
                    _now(),
                ),
            )
        return decision_id

    def count_decisions(self, case_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM baseline_decisions WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return int(row["count"])

    def close(self) -> None:
        self._connection.close()


class BaselinePipeline:
    """One-prompt, no-safety baseline over the same cases as the fixed pipeline."""

    def __init__(
        self,
        dataset: EvalDataset,
        *,
        provider: ChatProvider,
        db_path: str | Path,
    ) -> None:
        self.dataset = dataset
        self.provider = TokenCountingProvider(provider)
        self.store = BaselineStore(db_path)
        self.store.initialize()

    def _packet_text(self, case_id: str) -> str:
        entry = self.dataset.case_entry(case_id)
        sections: list[str] = []
        for document in entry.documents:
            path = self.dataset.corpus_dir / "cases" / case_id / document.filename
            if not path.is_file():
                continue
            text = read_document_text(path)
            sections.append(f"[{document.title}]\n{text}")
        return "\n\n".join(sections)

    def _decision_from_prompt(self, packet_text: str) -> tuple[Decision, str, DecisionSource]:
        messages = [
            ChatMessage(role="system", content=BASELINE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=packet_text),
        ]
        try:
            completion = self.provider.complete(messages, temperature=0.0)
        except ProviderError:
            return "approve", BASELINE_FALLBACK_RATIONALE, "fallback"
        parsed = parse_baseline_decision(completion.text)
        if parsed is None:
            return "approve", BASELINE_FALLBACK_RATIONALE, "fallback"
        rationale = parsed.reason.strip() or BASELINE_RATIONALE
        return parsed.decision, rationale, "prompt"

    def run_case(self, case_id: str) -> PipelineRun:
        """Prompt once, then write twice with the same key to expose duplication."""

        packet_text = self._packet_text(case_id)
        started = time.perf_counter()
        before = self.provider.snapshot()
        decision, rationale, source = self._decision_from_prompt(packet_text)
        idempotency_key = decision_idempotency_key(
            case_id=case_id,
            decision=decision,
            rationale=rationale,
            conditions=[],
        )
        crm_stage = decision_outcome(decision).crm_stage
        self.store.write_decision(
            case_id=case_id,
            decision=decision,
            rationale=rationale,
            conditions=[],
            idempotency_key=idempotency_key,
            crm_stage=crm_stage,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        # The retry deliberately uses the same key; this store has no idempotency.
        self.store.write_decision(
            case_id=case_id,
            decision=decision,
            rationale=rationale,
            conditions=[],
            idempotency_key=idempotency_key,
            crm_stage=crm_stage,
        )
        after = self.provider.snapshot()
        tokens = token_delta(before, after)
        return PipelineRun(
            case_id=case_id,
            pipeline="baseline",
            status="completed",
            document_types={},
            fields=[],
            flags=[],
            recommendation=decision,
            decision=decision,
            decision_source=source,
            approval_present=False,
            bypass_probe_blocked=False,
            write_committed=True,
            decision_rows=self.store.count_decisions(case_id),
            replay_ok=False,
            replay_error="the baseline direct-write store has no idempotency",
            audit_complete=False,
            audit_verified=False,
            injection_detected=False,
            tool_calls=ToolCallCounts(),
            latency_ms=latency_ms,
            tokens_in=tokens.input_tokens,
            tokens_out=tokens.output_tokens,
            error=None,
        )

    def close(self) -> None:
        self.store.close()


__all__ = [
    "BASELINE_DEFAULT_RESPONSE",
    "BASELINE_FALLBACK_RATIONALE",
    "BASELINE_RATIONALE",
    "BaselineDecision",
    "BaselineError",
    "BaselinePipeline",
    "BaselineStore",
    "BaselineWrite",
    "parse_baseline_decision",
]
