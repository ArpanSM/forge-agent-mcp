from __future__ import annotations

from pathlib import Path

import pytest

from forge_agent_mcp.evals.baseline import BaselinePipeline, BaselineStore
from forge_agent_mcp.evals.dataset import load_eval_dataset
from forge_agent_mcp.evals.fixed import FixedPipeline
from forge_agent_mcp.providers import FakeProvider
from tests.conftest import MANIFEST_PATH

CLEAN = "case_onb_ridgeline"
HARD_STOP = "case_onb_northgate"


def _dataset(generated_corpus):
    _manifest, corpus = generated_corpus
    return load_eval_dataset(MANIFEST_PATH, corpus.root)


def test_baseline_approves_every_packet_and_writes_twice_without_approval(
    generated_corpus, tmp_path: Path
) -> None:
    dataset = _dataset(generated_corpus)
    pipeline = BaselinePipeline(
        dataset,
        provider=FakeProvider(),
        db_path=tmp_path / "baseline.db",
    )

    run = pipeline.run_case(CLEAN)

    assert run.pipeline == "baseline"
    assert run.decision == "approve"
    assert run.recommendation == "approve"
    assert run.approval_present is False
    assert run.write_committed is True
    assert run.decision_rows == 2
    assert run.audit_complete is False
    assert run.document_types == {}
    assert run.fields == []
    assert run.tool_calls.attempted == 0
    assert run.tokens_in > 0
    assert pipeline.store.count_decisions(CLEAN) == 2
    pipeline.close()


def test_baseline_store_ignores_the_idempotency_key(tmp_path: Path) -> None:
    store = BaselineStore(tmp_path / "baseline.db")
    store.initialize()

    store.write_decision(
        case_id=CLEAN,
        decision="approve",
        rationale="first",
        conditions=[],
        idempotency_key="same-key",
        crm_stage="approved",
    )
    store.write_decision(
        case_id=CLEAN,
        decision="approve",
        rationale="first",
        conditions=[],
        idempotency_key="same-key",
        crm_stage="approved",
    )

    assert store.count_decisions(CLEAN) == 2
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
        assert clean.approval_present is True
        assert clean.bypass_probe_blocked is True
        assert clean.write_committed is True
        assert clean.decision_rows == 1
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
        assert hard.audit_complete is True
    finally:
        pipeline.close()


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
    with pytest.raises(Exception, match="required|hard-stop|case"):
        FixedPipeline(
            broken,
            provider=FakeProvider(),
            work_dir=tmp_path / "fixed",
        ).close()
