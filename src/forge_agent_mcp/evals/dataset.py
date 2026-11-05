"""Load the fixed eval dataset without letting labels reach a pipeline.

The manifest and generated ground truth are the oracle. The loader verifies the
12 + 4 split, every case, every hard-stop archetype and the ground-truth labels
before the runner is allowed to start.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ..corpus.manifest import Manifest, ManifestError, load_manifest
from ..corpus.models import CaseGroundTruth, CaseIndex, CorpusIndex
from ..corpus.vocab import Split
from .models import DocumentOracle, EvalCase, FieldOracle


class EvalDatasetError(RuntimeError):
    """The eval dataset is missing, inconsistent or unsafe to run."""


@dataclass(frozen=True)
class EvalDataset:
    manifest_path: Path
    corpus_dir: Path
    manifest: Manifest
    index: CorpusIndex
    cases: tuple[EvalCase, ...]
    manifest_hash: str

    def split(self, split: Split) -> list[EvalCase]:
        return [case for case in self.cases if case.split == split]

    @property
    def carrier_count(self) -> int:
        return len(self.manifest.carriers)


def _manifest_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_index(corpus_dir: Path) -> CorpusIndex:
    path = corpus_dir / "corpus.json"
    if not path.is_file():
        raise EvalDatasetError(
            f"corpus index not found at {path}; run `make corpus` before the eval"
        )
    try:
        return CorpusIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise EvalDatasetError(f"could not read corpus index {path}: {exc}") from exc


def _load_truth(corpus_dir: Path, filename: str) -> CaseGroundTruth:
    path = corpus_dir / filename
    if not path.is_file():
        raise EvalDatasetError(f"ground truth not found at {path}")
    try:
        return CaseGroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise EvalDatasetError(f"could not read ground truth {path}: {exc}") from exc


def _check_split(manifest: Manifest, index: CorpusIndex) -> None:
    manifest_ids = {case.case_id for case in manifest.cases}
    index_ids = {case.case_id for case in index.cases}
    if len(manifest.cases) != 16 or len(index.cases) != 16:
        raise EvalDatasetError(
            f"expected 16 cases in the manifest and corpus, found "
            f"{len(manifest.cases)} and {len(index.cases)}"
        )
    if manifest_ids != index_ids:
        missing = sorted(manifest_ids - index_ids)
        extra = sorted(index_ids - manifest_ids)
        raise EvalDatasetError(
            f"corpus and manifest case ids differ; missing={missing} extra={extra}"
        )
    splits = {
        split: sum(1 for case in manifest.cases if case.split == split)
        for split in ("eval", "held_out")
    }
    if splits != {"eval": 12, "held_out": 4}:
        raise EvalDatasetError(f"expected the 12+4 split, found {splits}")


def _check_hard_stops(manifest: Manifest) -> None:
    flags = manifest.flag_by_code
    eval_cases = [case for case in manifest.cases if case.split == "eval"]
    for archetype in manifest.required_hard_stop_archetypes:
        matching = [case for case in manifest.cases if archetype in case.archetypes]
        if not matching:
            raise EvalDatasetError(f"required hard-stop archetype {archetype!r} has no case")
        if not any(archetype in case.archetypes for case in eval_cases):
            raise EvalDatasetError(
                f"required hard-stop archetype {archetype!r} is only in the held-out split"
            )
        if not any(
            flag_code in flags and flags[flag_code].severity == "hard_stop"
            for case in eval_cases
            if archetype in case.archetypes
            for flag_code in case.expected_flags
        ):
            raise EvalDatasetError(
                f"required hard-stop archetype {archetype!r} has no hard-stop label"
            )


def _case_from_truth(
    manifest: Manifest,
    entry: CaseIndex,
    truth: CaseGroundTruth,
) -> EvalCase:
    manifest_case = next(
        (case for case in manifest.cases if case.case_id == truth.case_id),
        None,
    )
    if manifest_case is None:
        raise EvalDatasetError(f"ground truth names unknown case {truth.case_id!r}")
    if truth.expected_decision != manifest_case.expected_decision:
        raise EvalDatasetError(
            f"case {truth.case_id}: expected_decision in ground truth "
            f"{truth.expected_decision!r} disagrees with manifest "
            f"{manifest_case.expected_decision!r}"
        )
    if truth.expected_recommendation != manifest_case.expected_recommendation:
        raise EvalDatasetError(
            f"case {truth.case_id}: expected_recommendation disagrees with the manifest"
        )
    truth_flag_codes = [flag.code for flag in truth.expected_flags]
    if set(truth_flag_codes) != set(manifest_case.expected_flags):
        raise EvalDatasetError(f"case {truth.case_id}: expected_flags disagree with the manifest")
    if list(truth.expected_conditions) != list(manifest_case.expected_conditions):
        raise EvalDatasetError(
            f"case {truth.case_id}: expected_conditions disagree with the manifest"
        )

    truth_documents = {document.document_id: document.document_type for document in truth.documents}
    manifest_documents = {
        document.document_id: document.document_type for document in entry.documents
    }  # type: ignore[attr-defined]
    if truth_documents != manifest_documents:
        raise EvalDatasetError(
            f"case {truth.case_id}: document types disagree with the corpus index"
        )

    fields = [
        FieldOracle(
            field=field.field,
            value=field.value,
            document_id=field.citation.document_id or "",
            page=field.citation.page or 1,
            bbox=list(field.citation.bbox or [0.0, 0.0, 0.0, 0.0]),
        )
        for field in truth.expected_fields
    ]
    for field in fields:
        if not field.document_id or not field.bbox:
            raise EvalDatasetError(
                f"case {truth.case_id}: field {field.field!r} has no document citation"
            )

    severity_by_code = manifest.flag_by_code
    has_hard_stop = any(
        severity_by_code.get(code) is not None and severity_by_code[code].severity == "hard_stop"
        for code in truth_flag_codes
    )
    return EvalCase(
        case_id=truth.case_id,
        case_type=truth.case_type,
        split=truth.split,
        category=truth.category,
        archetypes=list(truth.archetypes),
        documents=[
            DocumentOracle(
                document_id=document.document_id,
                document_type=document.document_type,
            )
            for document in truth.documents
        ],
        expected_fields=fields,
        expected_flags=truth_flag_codes,
        expected_recommendation=truth.expected_recommendation,
        expected_decision=truth.expected_decision,
        expected_conditions=list(truth.expected_conditions),
        has_injection=(
            "prompt_injection" in truth.archetypes
            or "document_instruction_injection" in truth_flag_codes
        ),
        has_hard_stop=has_hard_stop,
    )


def load_eval_dataset(
    manifest_path: str | Path,
    corpus_dir: str | Path,
) -> EvalDataset:
    """Load and validate the fixed eval set, or refuse to run."""

    manifest_file = Path(manifest_path).resolve()
    corpus_root = Path(corpus_dir).resolve()
    try:
        manifest = load_manifest(manifest_file)
    except ManifestError as exc:
        raise EvalDatasetError(str(exc)) from exc
    index = _load_index(corpus_root)
    _check_split(manifest, index)
    _check_hard_stops(manifest)

    entries = {entry.case_id: entry for entry in index.cases}
    cases: list[EvalCase] = []
    for manifest_case in manifest.cases:
        entry = entries[manifest_case.case_id]
        truth = _load_truth(corpus_root, entry.ground_truth_file)
        cases.append(_case_from_truth(manifest, entry, truth))

    if len(cases) != 16:
        raise EvalDatasetError(f"expected 16 eval cases, loaded {len(cases)}")
    return EvalDataset(
        manifest_path=manifest_file,
        corpus_dir=corpus_root,
        manifest=manifest,
        index=index,
        cases=tuple(sorted(cases, key=lambda case: case.case_id)),
        manifest_hash=_manifest_hash(manifest_file),
    )


__all__ = ["EvalDataset", "EvalDatasetError", "load_eval_dataset"]
