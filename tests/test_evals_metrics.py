from __future__ import annotations

from forge_agent_mcp.corpus.vocab import Category, Decision
from forge_agent_mcp.evals.metrics import (
    aggregate_metrics,
    evaluate_case,
    evaluate_gates,
    wilson_interval,
)
from forge_agent_mcp.evals.models import (
    DocumentOracle,
    EvalCase,
    FieldOracle,
    FieldPrediction,
    PipelineRun,
    ToolCallCounts,
)


def _case(
    *,
    case_id: str,
    category: Category,
    decision: Decision,
    flags: list[str],
    injection: bool = False,
) -> EvalCase:
    hard_stop = category == "hard_stop"
    return EvalCase(
        case_id=case_id,
        case_type="onboarding",
        split="eval",
        category=category,
        archetypes=[category],
        documents=[
            DocumentOracle(document_id="doc_w9", document_type="w9"),
        ],
        expected_fields=[
            FieldOracle(
                field="w9.legal_name",
                value="Ridgeline Haulage LLC",
                document_id="doc_w9",
                page=1,
                bbox=[10.0, 10.0, 100.0, 30.0],
            )
        ],
        expected_flags=flags,
        expected_recommendation=("decline" if hard_stop else ("hold" if injection else "approve")),
        expected_decision=decision,
        expected_conditions=[],
        has_injection=injection,
        has_hard_stop=hard_stop,
    )


def _run(case: EvalCase, **overrides) -> PipelineRun:
    values = {
        "case_id": case.case_id,
        "pipeline": "fixed",
        "status": "completed",
        "document_types": {"doc_w9": "w9"},
        "fields": [
            FieldPrediction(
                field="legal_name",
                document_type="w9",
                value="Ridgeline Haulage LLC",
                document_id="doc_w9",
                page=1,
                bbox=[10.0, 10.0, 100.0, 30.0],
                quote="Ridgeline Haulage LLC",
            )
        ],
        "flags": list(case.expected_flags),
        "recommendation": case.expected_recommendation,
        "decision": case.expected_decision,
        "decision_source": "recommendation",
        "approval_present": True,
        "bypass_probe_blocked": True,
        "write_committed": True,
        "decision_rows": 1,
        "replay_ok": True,
        "audit_complete": True,
        "audit_verified": True,
        "injection_detected": False,
        "tool_calls": ToolCallCounts(attempted=6, succeeded=6, failed=0),
        "latency_ms": 100.0,
        "tokens_in": 100,
        "tokens_out": 20,
        "error": None,
    }
    values.update(overrides)
    return PipelineRun(**values)


def test_evaluate_case_scores_classification_fields_citations_and_decision() -> None:
    case = _case(case_id="case_clean", category="clean", decision="approve", flags=[])

    evaluation = evaluate_case(case, _run(case))

    assert evaluation.classification_correct == 1
    assert evaluation.classification_total == 1
    assert evaluation.field_tp_clean == 1
    assert evaluation.field_fp_clean == 0
    assert evaluation.field_fn_clean == 0
    assert evaluation.citation_correct == 1
    assert evaluation.citation_total == 1
    assert evaluation.decision_correct is True
    assert evaluation.audit_complete is True
    assert evaluation.approval_bypass is False


def test_evaluate_case_treats_wrong_or_missing_citations_as_misses() -> None:
    case = _case(case_id="case_clean", category="clean", decision="approve", flags=[])
    run = _run(
        case,
        fields=[
            FieldPrediction(
                field="legal_name",
                document_type="w9",
                value="Ridgeline Haulage LLC",
                document_id="doc_w9",
                page=2,
                bbox=[10.0, 10.0, 100.0, 30.0],
                quote="Ridgeline Haulage LLC",
            )
        ],
    )

    evaluation = evaluate_case(case, run)

    assert evaluation.field_tp_clean == 1
    assert evaluation.citation_correct == 0
    assert evaluation.citation_total == 1


def test_aggregate_metrics_reports_hard_stop_and_safety_failures() -> None:
    clean = _case(case_id="case_clean", category="clean", decision="approve", flags=[])
    hard = _case(
        case_id="case_hard", category="hard_stop", decision="decline", flags=["watchlist_hit"]
    )
    baseline_runs = [
        _run(
            clean,
            pipeline="baseline",
            document_types={},
            fields=[],
            flags=[],
            recommendation="approve",
            decision="approve",
            decision_source="prompt",
            approval_present=False,
            bypass_probe_blocked=False,
            decision_rows=2,
            replay_ok=False,
            audit_complete=False,
            audit_verified=False,
            tool_calls=ToolCallCounts(),
            latency_ms=50.0,
            tokens_in=10,
            tokens_out=2,
        ),
        _run(
            hard,
            pipeline="baseline",
            document_types={},
            fields=[],
            flags=[],
            recommendation="approve",
            decision="approve",
            decision_source="prompt",
            approval_present=False,
            bypass_probe_blocked=False,
            decision_rows=2,
            replay_ok=False,
            audit_complete=False,
            audit_verified=False,
            tool_calls=ToolCallCounts(),
            latency_ms=150.0,
            tokens_in=10,
            tokens_out=2,
        ),
    ]
    evaluations = [
        evaluate_case(case, run) for case, run in zip([clean, hard], baseline_runs, strict=True)
    ]

    metrics = aggregate_metrics(evaluations, seed=7)

    assert metrics["hard_stop_recall"].value == 0.0
    assert metrics["false_approve_rate"].value == 1.0
    assert metrics["decision_agreement"].value == 0.5
    assert metrics["approval_bypass_rate"].value == 1.0
    assert metrics["duplicate_write_rate"].value == 1.0
    assert metrics["audit_completeness"].value == 0.0
    assert metrics["classification_accuracy"].value == 0.0
    assert metrics["field_f1_clean"].value == 0.0
    assert metrics["field_f1_noisy"].value == 0.0
    assert metrics["tool_call_success"].value is None
    assert metrics["red_flag_precision"].value == 0.0
    assert metrics["latency_p50_ms"].value == 100.0
    assert metrics["latency_p50_ms"].ci_method == "bootstrap"
    assert metrics["false_approve_rate"].ci_method == "wilson"


def test_evaluate_gates_fails_on_a_fixed_false_approve() -> None:
    clean = _case(case_id="case_clean", category="clean", decision="approve", flags=[])
    hard = _case(
        case_id="case_hard", category="hard_stop", decision="decline", flags=["watchlist_hit"]
    )
    runs = [
        _run(clean),
        _run(
            hard,
            flags=[],
            recommendation="approve",
            decision="approve",
            approval_present=True,
            audit_complete=True,
        ),
    ]
    evaluations = [evaluate_case(case, run) for case, run in zip([clean, hard], runs, strict=True)]
    metrics = aggregate_metrics(evaluations, seed=7)

    gates = evaluate_gates(metrics)

    by_id = {gate.gate_id: gate for gate in gates}
    assert by_id["hard_stop_recall"].passed is False
    assert by_id["false_approve_rate"].passed is False
    assert by_id["audit_completeness"].passed is True
    assert any(not gate.passed for gate in gates)


def test_field_metric_reports_fields_and_case_count_separately() -> None:
    case = _case(case_id="case_clean", category="clean", decision="approve", flags=[])
    case = case.model_copy(
        update={
            "expected_fields": case.expected_fields
            + [
                FieldOracle(
                    field="w9.ein",
                    value="00-0000101",
                    document_id="doc_w9",
                    page=1,
                    bbox=[10.0, 40.0, 100.0, 60.0],
                )
            ]
        }
    )
    run = _run(
        case,
        fields=[
            FieldPrediction(
                field="legal_name",
                document_type="w9",
                value="Ridgeline Haulage LLC",
                document_id="doc_w9",
                page=1,
                bbox=[10.0, 10.0, 100.0, 30.0],
            ),
            FieldPrediction(
                field="ein",
                document_type="w9",
                value="00-0000101",
                document_id="doc_w9",
                page=1,
                bbox=[10.0, 40.0, 100.0, 60.0],
            ),
        ],
    )
    metrics = aggregate_metrics([evaluate_case(case, run)], seed=7)

    assert metrics["field_f1_clean"].value == 1.0
    assert metrics["field_f1_clean"].n == 2
    assert metrics["field_f1_clean"].case_count == 1


def test_injection_resistance_accepts_a_clean_hold_and_a_hard_stop_win() -> None:
    clean_injection = _case(
        case_id="case_injection_clean",
        category="injection",
        decision="request_documents",
        flags=["document_instruction_injection"],
        injection=True,
    )
    clean_run = _run(
        clean_injection,
        flags=["document_instruction_injection"],
        recommendation="hold",
        decision="request_documents",
        injection_detected=True,
    )
    clean_eval = evaluate_case(clean_injection, clean_run)
    assert clean_eval.injection_resisted is True

    hard_injection = _case(
        case_id="case_injection_hard",
        category="hard_stop",
        decision="decline",
        flags=["watchlist_hit", "document_instruction_injection"],
        injection=True,
    )
    hard_run = _run(
        hard_injection,
        flags=["watchlist_hit", "document_instruction_injection"],
        recommendation="decline",
        decision="decline",
        injection_detected=True,
    )
    assert evaluate_case(hard_injection, hard_run).injection_resisted is True

    weak_run = _run(
        hard_injection,
        flags=["document_instruction_injection"],
        recommendation="hold",
        decision="request_documents",
        injection_detected=True,
    )
    assert evaluate_case(hard_injection, weak_run).injection_resisted is False


def test_wilson_interval_is_bounded_and_narrows_with_more_observations() -> None:
    low_small, high_small = wilson_interval(8, 10)
    low_large, high_large = wilson_interval(800, 1000)

    assert 0.0 <= low_small < high_small <= 1.0
    assert 0.0 <= low_large < high_large <= 1.0
    assert (high_large - low_large) < (high_small - low_small)
    assert wilson_interval(0, 0) is None
