"""Deterministic corpus generation and CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CorpusError
from .labels import build_flag_truths, derive_flags
from .manifest import Manifest, ManifestError, load_manifest
from .models import (
    CaseGroundTruth,
    CaseIndex,
    CorpusIndex,
    DocumentEntry,
    FieldTruth,
    RecordEntry,
    SplitCounts,
)
from .pdf import DocumentSpec, RenderedPdf, hash_bytes, render_pdf
from .templates import RecordSpec, build_documents, build_records

DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "corpus" / "manifest.json"
DEFAULT_OUTPUT = Path("data") / "corpus"


@dataclass(frozen=True)
class GenerationResult:
    root: Path
    index: CorpusIndex
    cases: dict[str, CaseGroundTruth]


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(_json_text(payload), encoding="utf-8")


def _record_content_hash(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_pdf(
    path: Path,
    spec: DocumentSpec,
    *,
    manifest: Manifest,
    case_id: str,
    carrier_id: str,
) -> RenderedPdf:
    rendered = render_pdf(
        spec,
        factor_name=manifest.factor.name,
        case_id=case_id,
        carrier_id=carrier_id,
    )
    path.write_bytes(rendered.content)
    return rendered


def _write_record(path: Path, spec: RecordSpec) -> RecordEntry:
    content = _json_text(spec.data).encode("utf-8")
    path.write_bytes(content)
    return RecordEntry(
        record_id=spec.record_id,
        record_type=spec.record_type,
        title=spec.title,
        filename=spec.filename,
        content_hash=_record_content_hash(spec.data),
        sha256=hash_bytes(content),
    )


def generate_corpus(manifest: Manifest, output_dir: str | Path) -> GenerationResult:
    """Generate every packet, record, ground truth and index from the manifest."""

    root = Path(output_dir).resolve()
    if root == root.parent:
        raise CorpusError("refusing to write the corpus to the filesystem root")
    if root.exists():
        if not root.is_dir():
            raise CorpusError(f"output path exists and is not a directory: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    cases_root = root / "cases"
    cases_root.mkdir()

    index_cases: list[CaseIndex] = []
    ground_truths: dict[str, CaseGroundTruth] = {}
    carriers = manifest.carrier_by_id

    for case in manifest.cases:
        carrier = carriers[case.carrier_id]
        case_dir = cases_root / case.case_id
        (case_dir / "documents").mkdir(parents=True)
        (case_dir / "structured").mkdir()

        document_specs = build_documents(manifest, carrier, case)
        document_entries: list[DocumentEntry] = []
        field_citations = {}
        rendered_by_type = {}
        for spec in document_specs:
            rendered = _write_pdf(
                case_dir / spec.filename,
                spec,
                manifest=manifest,
                case_id=case.case_id,
                carrier_id=carrier.carrier_id,
            )
            rendered_by_type[spec.document_type] = rendered
            field_citations[spec.document_type] = rendered.citations
            document_entries.append(
                DocumentEntry(
                    document_id=spec.document_id,
                    document_type=spec.document_type,
                    title=spec.title,
                    filename=spec.filename,
                    pages=rendered.pages,
                    content_hash=rendered.content_hash,
                    sha256=rendered.sha256,
                )
            )

        record_specs = build_records(manifest, carrier, case)
        record_entries: list[RecordEntry] = []
        expected_records: dict[str, Any] = {}
        record_ids: dict[str, str] = {}
        for record in record_specs:
            entry = _write_record(case_dir / record.filename, record)
            record_entries.append(entry)
            expected_records[record.record_type] = record.data
            record_ids[record.record_type] = record.record_id

        invoice_hash = (
            rendered_by_type["invoice"].content_hash
            if "invoice" in rendered_by_type
            else None
        )
        derive_flags(manifest, carrier, case, invoice_content_hash=invoice_hash)

        expected_fields: list[FieldTruth] = []
        for spec in document_specs:
            citations = rendered_by_type[spec.document_type].citations
            for field in spec.fields:
                expected_fields.append(
                    FieldTruth(
                        field=f"{spec.document_type}.{field.key}",
                        value=field.value,
                        citation=citations[field.key],
                    )
                )

        truth = CaseGroundTruth(
            case_id=case.case_id,
            case_type=case.case_type,
            carrier_id=case.carrier_id,
            split=case.split,
            category=case.category,
            archetypes=case.archetypes,
            documents=document_entries,
            structured_records=record_entries,
            expected_fields=expected_fields,
            expected_records=expected_records,
            expected_flags=build_flag_truths(
                manifest,
                carrier,
                case,
                field_citations=field_citations,
                record_ids=record_ids,
            ),
            expected_recommendation=case.expected_recommendation,
            expected_decision=case.expected_decision,
            expected_conditions=case.expected_conditions,
            notes=case.notes,
        )
        ground_truths[case.case_id] = truth
        ground_truth_file = f"cases/{case.case_id}/ground_truth.json"
        _write_json(case_dir / "ground_truth.json", truth.model_dump(mode="json"))

        index_cases.append(
            CaseIndex(
                case_id=case.case_id,
                case_type=case.case_type,
                carrier_id=case.carrier_id,
                split=case.split,
                category=case.category,
                archetypes=case.archetypes,
                documents=document_entries,
                structured_records=record_entries,
                expected_flags=case.expected_flags,
                expected_recommendation=case.expected_recommendation,
                expected_decision=case.expected_decision,
                expected_conditions=case.expected_conditions,
                ground_truth_file=ground_truth_file,
            )
        )

    index = CorpusIndex(
        as_of=manifest.as_of,
        watermark=manifest.watermark,
        carrier_count=len(manifest.carriers),
        case_count=len(index_cases),
        split_counts=SplitCounts(
            eval=sum(case.split == "eval" for case in manifest.cases),
            held_out=sum(case.split == "held_out" for case in manifest.cases),
        ),
        cases=index_cases,
    )
    _write_json(root / "corpus.json", index.model_dump(mode="json"))
    return GenerationResult(root=root, index=index, cases=ground_truths)


def generate_from_path(
    manifest_path: str | Path, output_dir: str | Path
) -> GenerationResult:
    return generate_corpus(load_manifest(manifest_path), output_dir)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m forge_agent_mcp.corpus",
        description="Validate and generate the synthetic freight underwriting corpus.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate the committed manifest")
    validate.add_argument("--manifest", default=str(DEFAULT_MANIFEST))

    generate = subparsers.add_parser("generate", help="generate PDFs and ground truth")
    generate.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    generate.add_argument("--out", default=str(DEFAULT_OUTPUT))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "validate":
            print(
                f"manifest valid: {len(manifest.carriers)} carriers, "
                f"{len(manifest.cases)} cases"
            )
            return 0

        result = generate_corpus(manifest, args.out)
        pdf_count = sum(len(case.documents) for case in result.index.cases)
        record_count = sum(len(case.structured_records) for case in result.index.cases)
        print(
            f"generated {result.index.case_count} cases "
            f"({result.index.split_counts.eval} eval, "
            f"{result.index.split_counts.held_out} held out) in {result.root}"
        )
        print(f"  {pdf_count} PDFs, {record_count} structured records")
        print(f"  index: {result.root / 'corpus.json'}")
        return 0
    except (ManifestError, CorpusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
