from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from forge_agent_mcp.extraction.classify import classify_document


def test_classifies_every_document_in_the_corpus(generated_corpus) -> None:
    _manifest, result = generated_corpus
    correct = 0
    total = 0

    for case in result.index.cases:
        for document in case.documents:
            path = result.root / "cases" / case.case_id / document.filename
            classification = classify_document(
                path,
                document_id=document.document_id,
                title=document.title,
                filename=document.filename,
            )
            assert classification.document_type == document.document_type, (
                f"{case.case_id} {document.filename}: classified as {classification.document_type}"
            )
            assert classification.confidence >= 0.5
            correct += 1
            total += 1

    assert total == 70
    assert correct / total >= 0.95


def test_classification_survives_scan_noise(generated_corpus, tmp_path: Path) -> None:
    _manifest, result = generated_corpus
    case = next(case for case in result.index.cases if case.case_id == "case_onb_ridgeline")
    document = next(item for item in case.documents if item.document_type == "w9")
    source = result.root / "cases" / case.case_id / document.filename
    noisy = tmp_path / "noisy-w9.pdf"

    with pymupdf.open(source) as pdf:
        page = pdf[0]
        page.insert_text((54, 675), "SCAN NOISE ~ illegible fax stamp 0123456789")
        pdf.save(noisy)

    classification = classify_document(
        noisy,
        document_id=document.document_id,
        title=document.title,
        filename=document.filename,
    )

    assert classification.document_type == "w9"
    assert classification.confidence >= 0.5


def test_unknown_text_is_not_forced_into_a_known_type(tmp_path: Path) -> None:
    path = tmp_path / "unknown.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Delivery receipt for a package. Signature: A. Person")
    pdf.save(path)
    pdf.close()

    classification = classify_document(
        path,
        document_id="DOC-UNKNOWN",
        title="Delivery receipt",
        filename="documents/UNKNOWN.pdf",
    )

    assert classification.document_type == "unknown"
    assert classification.confidence == pytest.approx(0.0)
