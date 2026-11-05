from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from forge_agent_mcp.evals.dataset import EvalDatasetError, load_eval_dataset
from tests.conftest import MANIFEST_PATH


def test_dataset_loads_twelve_eval_and_four_held_out_cases(generated_corpus) -> None:
    _manifest, corpus = generated_corpus

    dataset = load_eval_dataset(MANIFEST_PATH, corpus.root)

    assert len(dataset.cases) == 16
    assert len(dataset.split("eval")) == 12
    assert len(dataset.split("held_out")) == 4
    assert len(dataset.manifest_hash) == 64
    ridgeline = next(
        case for case in dataset.manifest.cases if case.case_id == "case_onb_ridgeline"
    )
    assert ridgeline.split == "eval"
    clean = next(case for case in dataset.cases if case.case_id == "case_onb_ridgeline")
    assert clean.expected_decision == "approve"
    assert clean.documents
    assert clean.expected_fields
    assert clean.has_injection is False
    hard = next(case for case in dataset.cases if case.case_id == "case_onb_cedar_pine")
    assert hard.has_hard_stop is True


def test_dataset_refuses_to_start_when_a_case_is_missing(generated_corpus, tmp_path: Path) -> None:
    _manifest, corpus = generated_corpus
    corpus_dir = tmp_path / "corpus"
    shutil.copytree(corpus.root, corpus_dir)
    index_path = corpus_dir / "corpus.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["cases"] = [case for case in index["cases"] if case["case_id"] != "case_onb_meridian"]
    index["case_count"] = len(index["cases"])
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(EvalDatasetError, match="case_onb_meridian|16|12\\+4|split"):
        load_eval_dataset(MANIFEST_PATH, corpus_dir)


def test_dataset_refuses_when_ground_truth_is_missing(generated_corpus, tmp_path: Path) -> None:
    _manifest, corpus = generated_corpus
    corpus_dir = tmp_path / "corpus"
    shutil.copytree(corpus.root, corpus_dir)
    truth = corpus_dir / "cases" / "case_onb_ridgeline" / "ground_truth.json"
    truth.unlink()

    with pytest.raises(EvalDatasetError, match="ground truth|ground_truth"):
        load_eval_dataset(MANIFEST_PATH, corpus_dir)


def test_dataset_refuses_a_label_that_disagrees_with_the_manifest(
    generated_corpus, tmp_path: Path
) -> None:
    _manifest, corpus = generated_corpus
    corpus_dir = tmp_path / "corpus"
    shutil.copytree(corpus.root, corpus_dir)
    truth_path = corpus_dir / "cases" / "case_onb_ridgeline" / "ground_truth.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    truth["expected_decision"] = "decline"
    truth_path.write_text(json.dumps(truth), encoding="utf-8")

    with pytest.raises(EvalDatasetError, match="expected_decision"):
        load_eval_dataset(MANIFEST_PATH, corpus_dir)
