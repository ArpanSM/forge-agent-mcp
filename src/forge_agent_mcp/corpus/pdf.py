"""Deterministic synthetic PDF rendering with citations and content hashes."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass

from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from .models import Citation

PAGE_WIDTH, PAGE_HEIGHT = letter
LEFT = 54
VALUE_X = 190
VALUE_WIDTH = 345
TOP = 130
BOTTOM = 84
FONT = "Helvetica"
BOLD = "Helvetica-Bold"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    value: str


@dataclass(frozen=True)
class DocumentSpec:
    document_id: str
    document_type: str
    title: str
    carrier_name: str
    filename: str
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class RenderedPdf:
    content: bytes
    content_hash: str
    sha256: str
    pages: int
    citations: dict[str, Citation]


def content_hash_for(
    *,
    document_id: str,
    document_type: str,
    case_id: str,
    carrier_id: str,
    fields: tuple[FieldSpec, ...],
) -> str:
    payload = {
        "case_id": case_id,
        "carrier_id": carrier_id,
        "document_id": document_id,
        "document_type": document_type,
        "fields": [[field.key, field.value] for field in fields],
        "watermark": "SYNTHETIC",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wrap(value: str, font: str, size: float, width: float) -> list[str]:
    return simpleSplit(value, font, size, width) or [""]


def _draw_watermark(pdf: canvas.Canvas) -> None:
    pdf.saveState()
    pdf.setFillColorRGB(0.88, 0.88, 0.88)
    pdf.setFont(BOLD, 82)
    pdf.translate(80, 190)
    pdf.rotate(32)
    pdf.drawString(0, 0, "SYNTHETIC")
    pdf.restoreState()


def _draw_header(
    pdf: canvas.Canvas,
    *,
    spec: DocumentSpec,
    factor_name: str,
    case_id: str,
) -> None:
    pdf.setFillColorRGB(0.12, 0.12, 0.12)
    pdf.setFont(BOLD, 11)
    pdf.drawString(LEFT, PAGE_HEIGHT - 44, factor_name)
    pdf.setFont(FONT, 7.5)
    pdf.setFillColorRGB(0.40, 0.40, 0.40)
    pdf.drawRightString(
        PAGE_WIDTH - LEFT,
        PAGE_HEIGHT - 44,
        "SYNTHETIC DOCUMENT - NOT A REAL RECORD",
    )

    pdf.setFillColorRGB(0.05, 0.05, 0.05)
    pdf.setFont(BOLD, 16)
    pdf.drawString(LEFT, PAGE_HEIGHT - 74, spec.title)
    pdf.setFont(FONT, 8.5)
    pdf.drawString(LEFT, PAGE_HEIGHT - 90, f"Document ID: {spec.document_id}")
    pdf.drawString(LEFT, PAGE_HEIGHT - 102, f"Case: {case_id}    Carrier: {spec.carrier_name}")
    pdf.setStrokeColorRGB(0.72, 0.72, 0.72)
    pdf.line(LEFT, PAGE_HEIGHT - 110, PAGE_WIDTH - LEFT, PAGE_HEIGHT - 110)
    pdf.setFillColorRGB(0.05, 0.05, 0.05)


def _draw_footer(
    pdf: canvas.Canvas,
    *,
    document_id: str,
    content_hash: str,
    page_number: int,
) -> None:
    pdf.setStrokeColorRGB(0.72, 0.72, 0.72)
    pdf.line(LEFT, 70, PAGE_WIDTH - LEFT, 70)
    pdf.setFillColorRGB(0.30, 0.30, 0.30)
    pdf.setFont(FONT, 6.4)
    pdf.drawString(LEFT, 58, f"Document ID: {document_id}")
    pdf.drawString(LEFT, 48, f"Content hash: {content_hash}")
    pdf.drawRightString(PAGE_WIDTH - LEFT, 48, f"Page {page_number}")
    pdf.drawString(
        LEFT,
        37,
        "SYNTHETIC - all names, numbers and amounts are fictional. Not a real record.",
    )


def render_pdf(
    spec: DocumentSpec,
    *,
    factor_name: str,
    case_id: str,
    carrier_id: str,
) -> RenderedPdf:
    content_hash = content_hash_for(
        document_id=spec.document_id,
        document_type=spec.document_type,
        case_id=case_id,
        carrier_id=carrier_id,
        fields=spec.fields,
    )

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1)
    pdf.setTitle(spec.document_id)
    pdf.setAuthor(f"{factor_name} (synthetic)")
    pdf.setSubject("SYNTHETIC document - not a real record")
    pdf.setCreator("forge-agent-mcp corpus generator")
    pdf.setKeywords("synthetic, freight, underwriting")

    citations: dict[str, Citation] = {}
    page_number = 1
    y_top = TOP

    def start_page() -> None:
        _draw_watermark(pdf)
        _draw_header(pdf, spec=spec, factor_name=factor_name, case_id=case_id)

    def end_page() -> None:
        _draw_footer(
            pdf,
            document_id=spec.document_id,
            content_hash=content_hash,
            page_number=page_number,
        )

    start_page()
    for field in spec.fields:
        lines = _wrap(field.value, FONT, 10, VALUE_WIDTH)
        line_height = 13
        block_height = len(lines) * line_height
        if y_top + block_height + 10 > PAGE_HEIGHT - BOTTOM:
            end_page()
            pdf.showPage()
            page_number += 1
            start_page()
            y_top = TOP

        pdf.setFont(BOLD, 9)
        pdf.setFillColorRGB(0.18, 0.18, 0.18)
        pdf.drawString(LEFT, PAGE_HEIGHT - y_top - 9, field.label)

        pdf.setFont(FONT, 10)
        pdf.setFillColorRGB(0.05, 0.05, 0.05)
        width = 0.0
        for index, line in enumerate(lines):
            baseline = PAGE_HEIGHT - (y_top + index * line_height) - 9
            pdf.drawString(VALUE_X, baseline, line)
            width = max(width, pdfmetrics.stringWidth(line, FONT, 10))

        citations[field.key] = Citation(
            document_id=spec.document_id,
            page=page_number,
            bbox=[
                VALUE_X - 2,
                y_top - 3,
                VALUE_X + width + 2,
                y_top + block_height + 1,
            ],
        )
        y_top += block_height + 8

    end_page()
    pdf.showPage()
    pdf.save()

    content = buffer.getvalue()
    return RenderedPdf(
        content=content,
        content_hash=content_hash,
        sha256=hash_bytes(content),
        pages=page_number,
        citations=citations,
    )
