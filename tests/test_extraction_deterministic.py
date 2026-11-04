from __future__ import annotations

from pathlib import Path

from forge_agent_mcp.corpus.pdf import DocumentSpec, FieldSpec, render_pdf
from forge_agent_mcp.extraction.citations import verify_citation
from forge_agent_mcp.extraction.classify import classify_document
from forge_agent_mcp.extraction.deterministic import extract_document
from forge_agent_mcp.extraction.models import Classification


def test_deterministic_extraction_matches_ground_truth_for_every_corpus_document(
    generated_corpus,
) -> None:
    _manifest, result = generated_corpus

    for case in result.index.cases:
        truth = result.cases[case.case_id]
        for document in case.documents:
            path = result.root / "cases" / case.case_id / document.filename
            classification = classify_document(
                path,
                document_id=document.document_id,
                title=document.title,
                filename=document.filename,
            )
            extraction = extract_document(path, classification)
            expected = {
                field.field.split(".", 1)[1]: field
                for field in truth.expected_fields
                if field.field.startswith(f"{document.document_type}.")
            }

            assert extraction.status == "extracted", (
                f"{case.case_id} {document.document_type}: {extraction.error}"
            )
            assert set(extraction.fields) == set(expected)
            for key, expected_field in expected.items():
                extracted = extraction.fields[key]
                assert extracted.value == expected_field.value
                verify_citation(path, extracted.citation, extracted.value)


def test_extraction_finds_fields_that_continue_on_a_later_page(tmp_path: Path) -> None:
    labels = [
        ("legal_name", "Legal name"),
        ("dba", "DBA"),
        ("ein", "EIN"),
        ("mc_number", "MC number"),
        ("dot_number", "DOT number"),
        ("address", "Address"),
        ("phone", "Phone"),
        ("email", "Email"),
        ("owner_name", "Owner"),
        ("owner_email", "Owner email"),
        ("formation_date", "Formation date"),
        ("requested_advance", "Requested advance"),
        ("requested_reserve", "Requested reserve"),
    ]
    fields = tuple(
        FieldSpec(
            key=key,
            label=label,
            value=f"{label} value " + ("long synthetic text " * 18),
        )
        for key, label in labels
    )
    spec = DocumentSpec(
        document_id="DOC-MULTI-APP",
        document_type="application_kyc",
        title="Carrier Application / KYC",
        carrier_name="Multi Page Transport LLC",
        filename="documents/APP.pdf",
        fields=fields,
    )
    rendered = render_pdf(
        spec,
        factor_name="Northwind Freight Capital",
        case_id="case_multi",
        carrier_id="car_multi",
    )
    assert rendered.pages > 1
    path = tmp_path / "multi.pdf"
    path.write_bytes(rendered.content)
    classification = Classification(
        document_id=spec.document_id,
        document_type="application_kyc",
        title=spec.title,
        filename=spec.filename,
        confidence=1.0,
    )

    extraction = extract_document(path, classification)

    assert extraction.status == "extracted"
    assert {field.citation.page for field in extraction.fields.values()} == {1, 2}
    for field in extraction.fields.values():
        verify_citation(path, field.citation, field.value)


def test_missing_required_field_makes_the_document_needs_more_info(tmp_path: Path) -> None:
    spec = DocumentSpec(
        document_id="DOC-PARTIAL-W9",
        document_type="w9",
        title="IRS Form W-9 (Synthetic)",
        carrier_name="Partial Transport LLC",
        filename="documents/W9.pdf",
        fields=(
            FieldSpec(key="legal_name", label="Legal name", value="Partial Transport LLC"),
            FieldSpec(key="business_name", label="Business name", value="Partial Transport"),
        ),
    )
    rendered = render_pdf(
        spec,
        factor_name="Northwind Freight Capital",
        case_id="case_partial",
        carrier_id="car_partial",
    )
    path = tmp_path / "partial.pdf"
    path.write_bytes(rendered.content)
    classification = Classification(
        document_id=spec.document_id,
        document_type="w9",
        title=spec.title,
        filename=spec.filename,
        confidence=1.0,
    )

    extraction = extract_document(path, classification)

    assert extraction.status == "needs_more_info"
    assert extraction.error is not None
    assert "tax_classification" in extraction.error
    assert "ein" in extraction.error
    assert set(extraction.fields) == {"legal_name", "business_name"}
