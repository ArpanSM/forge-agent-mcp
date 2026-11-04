"""Readable page lines with coordinates, shared by parsing and model prompts."""

from __future__ import annotations

from dataclasses import dataclass

from .citations import DocumentText, Word

HEADER_BOTTOM = 115.0
FOOTER_TOP_MARGIN = 60.0
LINE_Y_TOLERANCE = 3.0


@dataclass(frozen=True)
class Line:
    page: int
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def left(self) -> float:
        return min(word.bbox[0] for word in self.words)

    @property
    def top(self) -> float:
        return min(word.bbox[1] for word in self.words)

    @property
    def bottom(self) -> float:
        return max(word.bbox[3] for word in self.words)

    @property
    def right(self) -> float:
        return max(word.bbox[2] for word in self.words)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            round(self.left, 2),
            round(self.top, 2),
            round(self.right, 2),
            round(self.bottom, 2),
        )


def page_lines(document: DocumentText) -> list[Line]:
    lines: list[Line] = []
    for page in document.pages:
        content = [
            word
            for word in page.words
            if word.text != "SYNTHETIC"
            and word.bbox[1] >= HEADER_BOTTOM
            and word.bbox[3] <= page.height - FOOTER_TOP_MARGIN
        ]
        content.sort(key=lambda word: ((word.bbox[1] + word.bbox[3]) / 2, word.bbox[0]))
        current: list[Word] = []
        anchor: float | None = None
        for word in content:
            center_y = (word.bbox[1] + word.bbox[3]) / 2
            if anchor is None or abs(center_y - anchor) <= LINE_Y_TOLERANCE:
                current.append(word)
                anchor = center_y if anchor is None else anchor
            else:
                lines.append(Line(page=page.number, words=tuple(sorted(current, key=_x))))
                current = [word]
                anchor = center_y
        if current:
            lines.append(Line(page=page.number, words=tuple(sorted(current, key=_x))))
    return lines


def evidence_text(document: DocumentText) -> str:
    """Page text with a bbox per line, so a model can cite coordinates it sees."""

    chunks: list[str] = []
    current_page: int | None = None
    for line in page_lines(document):
        if line.page != current_page:
            chunks.append(f"--- PAGE {line.page} ---")
            current_page = line.page
        x0, y0, x1, y1 = line.bbox
        chunks.append(f"[{x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f}] {line.text}")
    return "\n".join(chunks)


def _x(word: Word) -> float:
    return word.bbox[0]


__all__ = ["Line", "evidence_text", "page_lines"]
