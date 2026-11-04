from __future__ import annotations

from pathlib import Path

import pytest

from forge_agent_mcp.corpus.pdf import DocumentSpec, FieldSpec, render_pdf
from forge_agent_mcp.extraction.citations import (
    CitationError,
    DocumentReadError,
    verify_citation,
)
from forge_agent_mcp.extraction.models import Citation


def write_synthetic_pdf(
    tmp_path: Path, value: str = "Ridgeline Haulage LLC"
) -> tuple[Path, list[float]]:
    spec = DocumentSpec(
        document_id="DOC-TEST-APP",
        document_type="application_kyc",
        title="Carrier Application / KYC",
        carrier_name=value,
        filename="documents/APP.pdf",
        fields=(FieldSpec(key="legal_name", label="Legal name", value=value),),
    )
    rendered = render_pdf(
        spec,
        factor_name="Northwind Freight Capital",
        case_id="case_test",
        carrier_id="car_test",
    )
    path = tmp_path / "app.pdf"
    path.write_bytes(rendered.content)
    bbox = rendered.citations["legal_name"].bbox
    assert bbox is not None
    return path, bbox


def test_verify_citation_accepts_the_rendered_field_bbox(tmp_path: Path) -> None:
    path, bbox = write_synthetic_pdf(tmp_path)
    citation = Citation(
        document_id="DOC-TEST-APP",
        page=1,
        bbox=bbox,
        quote="Ridgeline Haulage LLC",
    )

    verify_citation(path, citation, value="Ridgeline Haulage LLC")


def test_verify_citation_rejects_a_bbox_that_misses_the_quote(tmp_path: Path) -> None:
    path, _bbox = write_synthetic_pdf(tmp_path)
    citation = Citation(
        document_id="DOC-TEST-APP",
        page=1,
        bbox=[0.0, 0.0, 10.0, 10.0],
        quote="Ridgeline Haulage LLC",
    )

    with pytest.raises(CitationError, match="does not contain"):
        verify_citation(path, citation, value="Ridgeline Haulage LLC")


def test_verify_citation_rejects_a_page_where_the_quote_is_absent(tmp_path: Path) -> None:
    path, bbox = write_synthetic_pdf(tmp_path)
    citation = Citation(
        document_id="DOC-TEST-APP",
        page=2,
        bbox=bbox,
        quote="Ridgeline Haulage LLC",
    )

    with pytest.raises(CitationError, match="page 2"):
        verify_citation(path, citation, value="Ridgeline Haulage LLC")


def test_verify_citation_rejects_a_value_that_does_not_match_the_quote(tmp_path: Path) -> None:
    path, bbox = write_synthetic_pdf(tmp_path)
    citation = Citation(
        document_id="DOC-TEST-APP",
        page=1,
        bbox=bbox,
        quote="Ridgeline Haulage LLC",
    )

    with pytest.raises(CitationError, match="does not match the quote exactly"):
        verify_citation(path, citation, value="Cedar & Pine Transport Inc.")


def test_verify_citation_rejects_a_truncated_value(tmp_path: Path) -> None:
    path, bbox = write_synthetic_pdf(tmp_path)
    citation = Citation(
        document_id="DOC-TEST-APP",
        page=1,
        bbox=bbox,
        quote="Ridgeline Haulage LLC",
    )

    with pytest.raises(CitationError, match="does not match the quote exactly"):
        verify_citation(path, citation, value="LLC")


def test_verify_citation_rejects_a_page_sized_bbox(tmp_path: Path) -> None:
    path, _bbox = write_synthetic_pdf(tmp_path)
    citation = Citation(
        document_id="DOC-TEST-APP",
        page=1,
        bbox=[0.0, 0.0, 612.0, 792.0],
        quote="Ridgeline Haulage LLC",
    )

    with pytest.raises(CitationError, match="more than half the page"):
        verify_citation(path, citation, value="Ridgeline Haulage LLC")


def test_missing_document_is_a_document_read_error(tmp_path: Path) -> None:
    citation = Citation(
        document_id="DOC-TEST-APP",
        page=1,
        bbox=[190.0, 127.0, 300.0, 145.0],
        quote="Ridgeline Haulage LLC",
    )

    with pytest.raises(DocumentReadError):
        verify_citation(tmp_path / "absent.pdf", citation, value="Ridgeline Haulage LLC")


def test_citation_rejects_an_inverted_bbox() -> None:
    with pytest.raises(ValueError, match="bbox"):
        Citation(
            document_id="DOC-TEST-APP",
            page=1,
            bbox=[200.0, 200.0, 100.0, 100.0],
            quote="Ridgeline Haulage LLC",
        )
