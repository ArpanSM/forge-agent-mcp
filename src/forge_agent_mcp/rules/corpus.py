"""Load the structured records and document metadata for one corpus case."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ..corpus.models import CorpusIndex, DocumentEntry, RecordPayload
from ..corpus.vocab import CaseType, StrictModel

CORPUS_INDEX = "corpus.json"
RECORD_ADAPTER = TypeAdapter(RecordPayload)


class RuleEngineError(RuntimeError):
    """A case packet could not be loaded for rule evaluation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class CasePacket(StrictModel):
    case_id: str
    case_type: CaseType
    as_of: date
    documents: list[DocumentEntry]
    records: list[RecordPayload]

    def record(self, record_type: str) -> RecordPayload | None:
        return next(
            (record for record in self.records if record.record_type == record_type),
            None,
        )

    def document_by_type(self, document_type: str) -> DocumentEntry | None:
        return next(
            (document for document in self.documents if document.document_type == document_type),
            None,
        )

    @property
    def document_type_by_id(self) -> dict[str, str]:
        return {document.document_id: document.document_type for document in self.documents}


def _read_index(root: Path) -> CorpusIndex:
    path = root / CORPUS_INDEX
    if not path.is_file():
        raise RuleEngineError(
            "corpus_not_found",
            f"corpus index not found at {path}; generate it with `make corpus`",
        )
    try:
        return CorpusIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise RuleEngineError(
            "corpus_invalid",
            f"could not read corpus index {path}: {exc}",
        ) from exc


def _read_record(root: Path, case_id: str, filename: str) -> RecordPayload:
    path = root / "cases" / case_id / filename
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RECORD_ADAPTER.validate_python(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise RuleEngineError(
            "corpus_invalid",
            f"could not read structured record {path}: {exc}",
        ) from exc


def load_case_packet(case_id: str, corpus_dir: str | Path) -> CasePacket:
    """Read the index, document hashes and typed records for one case."""

    root = Path(corpus_dir)
    index = _read_index(root)
    case = next((item for item in index.cases if item.case_id == case_id), None)
    if case is None:
        raise RuleEngineError("unknown_case", f"unknown case {case_id!r} in {root / CORPUS_INDEX}")

    records = [_read_record(root, case_id, entry.filename) for entry in case.structured_records]
    return CasePacket(
        case_id=case.case_id,
        case_type=case.case_type,
        as_of=index.as_of,
        documents=case.documents,
        records=records,
    )


__all__ = ["CasePacket", "RuleEngineError", "load_case_packet"]
