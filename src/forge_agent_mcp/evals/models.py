"""Typed evaluation records and report contracts.

The oracle models are populated from the manifest and generated ground truth;
pipeline runs contain only what the pipeline produced. Keeping them separate is
what prevents labels from becoming pipeline input.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..corpus.vocab import (
    CaseType,
    Category,
    Decision,
    Recommendation,
    Split,
    StrictModel,
)

PipelineName = Literal["baseline", "fixed"]
CiMethod = Literal["wilson", "bootstrap", "none"]
MetricUnit = Literal["ratio", "ms", "usd"]
Comparator = Literal["<=", ">="]


class DocumentOracle(StrictModel):
    document_id: str
    document_type: str


class FieldOracle(StrictModel):
    """One expected field with its ground-truth citation."""

    field: str
    value: str
    document_id: str
    page: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)


class EvalCase(StrictModel):
    """The oracle for one case. This object is never passed to a pipeline."""

    case_id: str
    case_type: CaseType
    split: Split
    category: Category
    archetypes: list[str]
    documents: list[DocumentOracle]
    expected_fields: list[FieldOracle]
    expected_flags: list[str]
    expected_recommendation: Recommendation
    expected_decision: Decision
    expected_conditions: list[str]
    has_injection: bool
    has_hard_stop: bool


class FieldPrediction(StrictModel):
    field: str
    document_type: str
    value: str
    document_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    quote: str | None = None


class ToolCallCounts(StrictModel):
    attempted: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)

    @property
    def success_rate(self) -> float | None:
        if not self.attempted:
            return None
        return self.succeeded / self.attempted


class PipelineRun(StrictModel):
    """One case's observable output from one pipeline."""

    case_id: str
    pipeline: PipelineName
    status: Literal["completed", "failed"]
    document_types: dict[str, str] = Field(default_factory=dict)
    fields: list[FieldPrediction] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    decision: Decision | None = None
    approval_present: bool = False
    bypass_probe_blocked: bool = False
    write_committed: bool = False
    decision_rows: int = Field(default=0, ge=0)
    audit_complete: bool = False
    audit_verified: bool = False
    injection_detected: bool = False
    tool_calls: ToolCallCounts = Field(default_factory=ToolCallCounts)
    latency_ms: float = Field(default=0.0, ge=0.0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    error: str | None = None


class CaseEvaluation(StrictModel):
    case_id: str
    split: Split
    pipeline: PipelineName
    classification_correct: int = Field(default=0, ge=0)
    classification_total: int = Field(default=0, ge=0)
    field_tp_clean: int = Field(default=0, ge=0)
    field_fp_clean: int = Field(default=0, ge=0)
    field_fn_clean: int = Field(default=0, ge=0)
    field_tp_noisy: int = Field(default=0, ge=0)
    field_fp_noisy: int = Field(default=0, ge=0)
    field_fn_noisy: int = Field(default=0, ge=0)
    citation_correct: int = Field(default=0, ge=0)
    citation_total: int = Field(default=0, ge=0)
    hard_stop_case: bool = False
    hard_stop_caught: bool = False
    false_approve: bool = False
    flag_tp: int = Field(default=0, ge=0)
    flag_fp: int = Field(default=0, ge=0)
    flag_fn: int = Field(default=0, ge=0)
    decision_correct: bool = False
    write_committed: bool = False
    approval_bypass: bool = False
    duplicate_write: bool = False
    audit_complete: bool = False
    injection_case: bool = False
    injection_resisted: bool = False
    tool_attempted: int = Field(default=0, ge=0)
    tool_succeeded: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    error: str | None = None


class MetricResult(StrictModel):
    """One metric with its sample size and interval."""

    name: str
    value: float | None = None
    n: int = Field(default=0, ge=0)
    numerator: float | None = None
    denominator: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    ci_method: CiMethod = "none"
    unit: MetricUnit = "ratio"
    notes: str | None = None


class MetricPair(StrictModel):
    metric: str
    baseline: MetricResult
    fixed: MetricResult


class PipelineSummary(StrictModel):
    pipeline: PipelineName
    cases: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    decisions: int = Field(ge=0)
    approvals: int = Field(ge=0)
    write_commits: int = Field(ge=0)
    duplicate_writes: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    tool_calls_attempted: int = Field(ge=0)
    tool_calls_succeeded: int = Field(ge=0)
    tool_calls_failed: int = Field(ge=0)
    duration_ms_total: float = Field(ge=0.0)
    estimated_usd: float = Field(ge=0.0)


class SplitMetrics(StrictModel):
    split: Split
    case_count: int = Field(ge=0)
    metrics: dict[str, MetricPair]
    baseline: PipelineSummary
    fixed: PipelineSummary


class HardGate(StrictModel):
    gate_id: str
    metric: str
    comparator: Comparator
    threshold: float
    baseline_value: float | None = None
    baseline_passed: bool = False
    value: float | None = None
    passed: bool = False
    detail: str


class FailureItem(StrictModel):
    pipeline: PipelineName
    case_id: str
    split: Split
    category: Category
    reason: str
    expected: str
    actual: str


class CaseComparison(StrictModel):
    case_id: str
    split: Split
    case_type: CaseType
    category: Category
    archetypes: list[str]
    expected_recommendation: Recommendation
    expected_decision: Decision
    expected_flags: list[str]
    baseline: PipelineRun
    fixed: PipelineRun


class ProviderInfo(StrictModel):
    name: str
    model: str
    mode: Literal["offline", "live"]
    details: str


class DatasetInfo(StrictModel):
    manifest_path: str
    manifest_hash: str
    corpus_dir: str
    as_of: str
    eval_cases: int
    held_out_cases: int
    carrier_count: int


class EvalReport(StrictModel):
    schema_version: int = 1
    generated_at: datetime
    provider: ProviderInfo
    dataset: DatasetInfo
    eval: SplitMetrics
    held_out: SplitMetrics
    gates: list[HardGate]
    release_gate: Literal["passed", "failed"]
    cases: list[CaseComparison]
    failure_gallery: list[FailureItem]
    caveats: list[str] = Field(default_factory=list)


__all__ = [
    "CaseComparison",
    "CaseEvaluation",
    "DatasetInfo",
    "DocumentOracle",
    "EvalCase",
    "EvalReport",
    "FailureItem",
    "FieldOracle",
    "FieldPrediction",
    "HardGate",
    "MetricPair",
    "MetricResult",
    "PipelineName",
    "PipelineRun",
    "PipelineSummary",
    "ProviderInfo",
    "SplitMetrics",
    "ToolCallCounts",
]
