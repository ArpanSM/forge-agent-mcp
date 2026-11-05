from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from forge_agent_mcp.corpus.vocab import Decision
from forge_agent_mcp.evals.baseline import BaselinePipeline, BaselineStore
from forge_agent_mcp.evals.dataset import load_eval_dataset
from forge_agent_mcp.evals.fixed import (
    FixedPipeline,
    FixedPipelineError,
    audit_chain_errors,
    audit_is_complete,
)
from forge_agent_mcp.evals.runner import EvalConfig, run_evals
from forge_agent_mcp.providers import FakeProvider
from tests.conftest import MANIFEST_PATH

CLEAN = "case_onb_ridgeline"
HARD_STOP = "case_onb_northgate"
INJECTION = "case_onb_meridian"


def _dataset(generated_corpus):
    _manifest, corpus = generated_corpus
    return load_eval_dataset(MANIFEST_PATH, corpus.root)


def test_baseline_uses_a_parsed_prompt_decision(generated_corpus, tmp_path: Path) -> None:
    dataset = _dataset(generated_corpus)
    provider = FakeProvider(
        text=json.dumps(
            {
                "decision": "decline",
                "reason": "The packet is too risky for direct funding.",
            }
        )
    )
    pipeline = BaselinePipeline(
        dataset,
        provider=provider,
        db_path=tmp_path / "baseline.db",
    )

    run = pipeline.run_case(CLEAN)

    assert run.pipeline == "baseline"
    assert run.decision == "decline"
    assert run.recommendation == "decline"
    assert run.decision_source == "prompt"
    assert run.approval_present is False
    assert run.write_committed is True
    assert run.decision_rows == 2
    assert run.replay_ok is False
    assert run.audit_complete is False
    assert run.document_types == {}
    assert run.fields == []
    assert run.tool_calls.attempted == 0
    assert run.tokens_in > 0
    assert pipeline.store.count_decisions(CLEAN) == 2
    pipeline.close()


def test_baseline_marks_an_unparseable_prompt_as_a_fallback(
    generated_corpus, tmp_path: Path
) -> None:
    dataset = _dataset(generated_corpus)
    pipeline = BaselinePipeline(
        dataset,
        provider=FakeProvider(text="fake completion"),
        db_path=tmp_path / "baseline.db",
    )

    run = pipeline.run_case(CLEAN)

    assert run.decision == "approve"
    assert run.decision_source == "fallback"
    pipeline.close()


def test_baseline_store_validates_inputs(tmp_path: Path) -> None:
    store = BaselineStore(tmp_path / "baseline.db")
    store.initialize()

    with pytest.raises(ValidationError):
        store.write_decision(
            case_id="",
            decision="approve",
            rationale="invalid case id",
            conditions=[],
            idempotency_key="key",
            crm_stage="approved",
        )
    with pytest.raises(ValidationError):
        store.write_decision(
            case_id=CLEAN,
            decision=cast("Decision", "not-a-decision"),
            rationale="invalid decision",
            conditions=[],
            idempotency_key="key",
            crm_stage="approved",
        )
    store.close()


def test_fixed_pipeline_uses_the_real_gateway_and_a_minted_approval(
    generated_corpus, tmp_path: Path
) -> None:
    dataset = _dataset(generated_corpus)
    pipeline = FixedPipeline(
        dataset,
        provider=FakeProvider(),
        work_dir=tmp_path / "fixed",
    )
    try:
        clean = pipeline.run_case(CLEAN)

        assert clean.pipeline == "fixed"
        assert clean.status == "completed"
        assert clean.decision == "approve"
        assert clean.decision_source == "recommendation"
        assert clean.approval_present is True
        assert clean.bypass_probe_blocked is True
        assert clean.write_committed is True
        assert clean.decision_rows == 1
        assert clean.replay_ok is True
        assert clean.replay_error is None
        assert clean.audit_complete is True
        assert clean.audit_verified is True
        assert clean.document_types
        assert clean.fields
        assert clean.tool_calls.attempted > 0
        assert clean.tool_calls.succeeded == clean.tool_calls.attempted
        assert clean.tokens_in > 0

        hard = pipeline.run_case(HARD_STOP)

        assert hard.decision == "decline"
        assert hard.approval_present is True
        assert hard.bypass_probe_blocked is True
        assert hard.decision_rows == 1
        assert hard.replay_ok is True
        assert hard.audit_complete is True

        probes = {probe.name: probe for probe in pipeline.bypass_probes}
        assert set(probes) == {
            "blank",
            "expired",
            "mismatched_case",
            "mismatched_decision",
            "wrong_payload",
            "reused",
        }
        assert all(probe.blocked for probe in probes.values())
        assert probes["blank"].actual_code == "approval_required"
        assert probes["expired"].actual_code == "approval_expired"
        assert probes["reused"].actual_code == "approval_reused"
    finally:
        pipeline.close()


def test_audit_chain_rejects_a_missing_recommendation_event(
    generated_corpus, tmp_path: Path
) -> None:
    dataset = _dataset(generated_corpus)
    pipeline = FixedPipeline(
        dataset,
        provider=FakeProvider(),
        work_dir=tmp_path / "fixed",
    )
    try:
        pipeline.run_case(CLEAN)
        decision = pipeline.store.latest_decision("tenant_eval", CLEAN)
        assert decision is not None
        events = pipeline.agent_client.get_audit(CLEAN, limit=500).events
        trimmed = [
            event
            for event in events
            if not (event.tool == "record_recommendation" and event.event == "write")
        ]
        expected_documents = {
            document.document_id for document in dataset.case_entry(CLEAN).documents
        }

        errors = audit_chain_errors(
            trimmed,
            decision_id=decision.decision_id,
            decision=decision.decision,
            expected_document_ids=expected_documents,
            replay_required=True,
        )

        assert "record_recommendation event is missing" in errors
        assert (
            audit_is_complete(
                trimmed,
                decision_id=decision.decision_id,
                decision=decision.decision,
                expected_document_ids=expected_documents,
                replay_required=True,
            )
            is False
        )
    finally:
        pipeline.close()


def test_replay_error_fails_the_duplicate_gate_and_release_gate(
    generated_corpus, tmp_path: Path, monkeypatch
) -> None:
    _manifest, corpus = generated_corpus
    monkeypatch.setattr(
        FixedPipeline,
        "_replay_write",
        lambda self, result: (False, "forced replay error"),
    )

    report = run_evals(
        EvalConfig(
            manifest_path=MANIFEST_PATH,
            corpus_dir=corpus.root,
            out_dir=tmp_path / "reports",
            case_ids=(CLEAN, HARD_STOP, INJECTION),
            allow_subset=True,
            seed=7,
        )
    )

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["duplicate_write_rate"].passed is False
    assert report.eval.fixed.duplicate_writes == 3
    assert report.release_gate == "failed"
    assert any(
        item.pipeline == "fixed" and "replay" in item.reason for item in report.failure_gallery
    )


def test_fixed_pipeline_refuses_to_start_without_the_required_archetypes(
    generated_corpus, tmp_path: Path
) -> None:
    dataset = _dataset(generated_corpus)
    broken = dataset.__class__(
        manifest_path=dataset.manifest_path,
        corpus_dir=dataset.corpus_dir,
        manifest=dataset.manifest,
        index=dataset.index,
        cases=tuple(case for case in dataset.cases if case.case_id != HARD_STOP),
        manifest_hash=dataset.manifest_hash,
    )
    with pytest.raises(FixedPipelineError, match="does not contain every manifest case"):
        FixedPipeline(
            broken,
            provider=FakeProvider(),
            work_dir=tmp_path / "fixed",
        ).close()
