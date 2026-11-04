from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf
import pytest

from forge_agent_mcp.corpus import generator
from forge_agent_mcp.corpus.manifest import load_manifest

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "corpus" / "manifest.json"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b\d{3}-\d{4}\b")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("corpus")
    manifest = load_manifest(MANIFEST_PATH)
    result = generator.generate_corpus(manifest, root / "out")
    return manifest, result


def test_generator_writes_the_fixed_corpus(generated) -> None:
    manifest, result = generated
    index = result.index

    assert index.carrier_count == 8
    assert index.case_count == 16
    assert index.split_counts.eval == 12
    assert index.split_counts.held_out == 4
    assert len(index.cases) == 16

    pdfs = sorted(result.root.rglob("*.pdf"))
    records = sorted(result.root.rglob("*.json"))
    assert len(pdfs) == 71
    # 32 structured records, 16 case ground-truth files and one corpus index.
    assert len(records) == 49

    for case in index.cases:
        expected = next(item for item in manifest.cases if item.case_id == case.case_id)
        assert case.split == expected.split
        assert case.category == expected.category
        assert case.expected_flags == expected.expected_flags
        assert (result.root / case.ground_truth_file).exists()


def test_generation_is_deterministic(generated) -> None:
    _manifest, result = generated
    before = _file_hashes(result.root)

    generator.generate_corpus(load_manifest(MANIFEST_PATH), result.root)
    after = _file_hashes(result.root)

    assert before == after


def test_generated_index_matches_corpus_json(generated) -> None:
    _manifest, result = generated
    raw = json.loads((result.root / "corpus.json").read_text(encoding="utf-8"))

    assert raw == result.index.model_dump(mode="json")
    assert raw["watermark"] == "SYNTHETIC"
    assert raw["split_counts"] == {"eval": 12, "held_out": 4}


def test_every_pdf_is_watermarked_and_carries_its_id_and_hash(generated) -> None:
    _manifest, result = generated

    for case in result.index.cases:
        for document in case.documents:
            path = result.root / "cases" / case.case_id / document.filename
            data = path.read_bytes()
            assert hashlib.sha256(data).hexdigest() == document.sha256

            with pymupdf.open(path) as pdf:
                assert pdf.page_count == document.pages
                text = "\n".join(page.get_text() for page in pdf)
            assert "SYNTHETIC" in text
            assert document.document_id in text
            assert document.content_hash in text


def test_every_extracted_field_has_a_bbox_that_contains_the_source(generated) -> None:
    _manifest, result = generated

    for case in result.index.cases:
        truth = result.cases[case.case_id]
        documents = {document.document_id: document for document in case.documents}
        for field in truth.expected_fields:
            citation = field.citation
            assert citation.document_id in documents
            assert citation.page is not None and citation.page >= 1
            assert citation.bbox is not None and len(citation.bbox) == 4
            x0, y0, x1, y1 = citation.bbox
            assert x0 < x1 and y0 < y1

            document = documents[citation.document_id]
            path = result.root / "cases" / case.case_id / document.filename
            with pymupdf.open(path) as pdf:
                page = pdf[citation.page - 1]
                text = page.get_text()
                needle = field.value if len(field.value) <= 60 else field.value[:40]
                assert needle in text
                hits = page.search_for(needle)
                assert any(
                    pymupdf.Rect(*citation.bbox).intersects(hit) for hit in hits
                ), f"{case.case_id} {field.field}: bbox does not contain {needle!r}"


def test_ground_truth_flags_have_severity_and_evidence(generated) -> None:
    manifest, result = generated
    catalog = {flag.code: flag for flag in manifest.flag_catalog}
    manifest_cases = {case.case_id: case for case in manifest.cases}

    for case_id, truth in result.cases.items():
        expected = manifest_cases[case_id]
        assert [flag.code for flag in truth.expected_flags] == expected.expected_flags
        assert truth.expected_recommendation == expected.expected_recommendation
        assert truth.expected_decision == expected.expected_decision
        assert truth.expected_conditions == expected.expected_conditions

        document_ids = {document.document_id for document in truth.documents}
        for flag in truth.expected_flags:
            assert flag.severity == catalog[flag.code].severity
            assert flag.rule == catalog[flag.code].rule
            if flag.code == "missing_document":
                assert flag.evidence.missing_document_type == "proof_of_delivery"
            elif flag.code == "watchlist_hit":
                assert flag.evidence.record_path == "matches[0]"
                assert flag.evidence.document_id is not None
            else:
                assert flag.evidence.document_id in document_ids
                assert flag.evidence.page == 1
                assert flag.evidence.bbox is not None


def test_structured_records_are_synthetic_and_hashed(generated) -> None:
    _manifest, result = generated

    for case in result.index.cases:
        for record in case.structured_records:
            path = result.root / "cases" / case.case_id / record.filename
            data = json.loads(path.read_text(encoding="utf-8"))
            assert hashlib.sha256(path.read_bytes()).hexdigest() == record.sha256
            assert data["synthetic"] is True
            assert data["record_id"] == record.record_id

    harborline = result.cases["case_onb_harborline"]
    assert harborline.expected_records["watchlist"]["matches"]
    sagebrush = result.cases["case_inv_sagebrush"]
    assert sagebrush.expected_records["prior_invoices"]["invoices"][0]["invoice_number"] == (
        "INV-SAG-1004"
    )


def test_all_contact_details_are_synthetic(generated) -> None:
    _manifest, result = generated

    for case in result.index.cases:
        for document in case.documents:
            path = result.root / "cases" / case.case_id / document.filename
            with pymupdf.open(path) as pdf:
                text = "\n".join(page.get_text() for page in pdf)
            assert "TODO" not in text
            for email in EMAIL_RE.findall(text):
                assert email.endswith(".example.com"), email
            for phone in PHONE_RE.findall(text):
                assert phone.startswith("555-01"), phone


def test_cli_validates_and_generates(tmp_path: Path) -> None:
    assert generator.main(["validate", "--manifest", str(MANIFEST_PATH)]) == 0

    out = tmp_path / "cli-corpus"
    assert (
        generator.main(
            ["generate", "--manifest", str(MANIFEST_PATH), "--out", str(out)]
        )
        == 0
    )
    assert (out / "corpus.json").exists()
    assert len(list(out.rglob("*.pdf"))) == 71
