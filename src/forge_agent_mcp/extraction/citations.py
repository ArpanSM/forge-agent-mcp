"""Citation verification against the source PDF.

A citation is only accepted when its page contains the quote, the extracted value
is contained in that quote, and the bbox covers the quoted words. This is what
makes "never guess a value" enforceable rather than a prompt request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .models import BBox, Citation

POINT_TOLERANCE = 1.5


class CitationError(ValueError):
    """A citation does not point at the text it claims to quote."""


def normalize_space(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class Word:
    text: str
    bbox: BBox


@dataclass(frozen=True)
class PageText:
    number: int
    text: str
    words: tuple[Word, ...]


@dataclass(frozen=True)
class DocumentText:
    path: Path
    pages: tuple[PageText, ...]

    @classmethod
    def load(cls, path: str | Path) -> DocumentText:
        source = Path(path)
        try:
            with pymupdf.open(source) as pdf:
                pages = tuple(
                    PageText(
                        number=index,
                        text=normalize_space(page.get_text("text")),
                        words=tuple(
                            Word(text=word[4], bbox=tuple(word[:4]))  # type: ignore[arg-type]
                            for word in page.get_text("words")
                        ),
                    )
                    for index, page in enumerate(pdf, start=1)
                )
        except (pymupdf.FileNotFoundError, RuntimeError) as exc:
            raise CitationError(f"could not read document {source}: {exc}") from exc
        return cls(path=source, pages=pages)

    def verify(self, citation: Citation, value: str) -> None:
        if citation.page > len(self.pages):
            raise CitationError(
                f"citation points to page {citation.page} but the document has "
                f"{len(self.pages)} page(s)"
            )

        page = self.pages[citation.page - 1]
        quote = normalize_space(citation.quote)
        if quote not in page.text:
            raise CitationError(f"quoted text is not present on page {citation.page}")

        normalized_value = normalize_space(value)
        if normalized_value not in quote:
            raise CitationError(f"value {value!r} is not contained in the quote")

        tokens = quote.split()
        runs = list(_find_token_runs(page.words, tokens))
        if not runs:
            raise CitationError(
                f"quoted text could not be located as a contiguous phrase on page {citation.page}"
            )

        for matched in runs:
            if all(_word_center_inside(citation.bbox, word) for word in matched):
                return
        raise CitationError(
            f"citation bbox does not contain the quoted text on page {citation.page}"
        )


def _contains(bbox: BBox, x: float, y: float) -> bool:
    x0, y0, x1, y1 = bbox
    return (
        x0 - POINT_TOLERANCE <= x <= x1 + POINT_TOLERANCE
        and y0 - POINT_TOLERANCE <= y <= y1 + POINT_TOLERANCE
    )


def _word_center_inside(bbox: BBox, word: Word) -> bool:
    center_x = (word.bbox[0] + word.bbox[2]) / 2
    center_y = (word.bbox[1] + word.bbox[3]) / 2
    return _contains(bbox, center_x, center_y)


def _find_token_runs(words: tuple[Word, ...], tokens: list[str]) -> list[tuple[Word, ...]]:
    if not tokens:
        return []
    width = len(tokens)
    runs: list[tuple[Word, ...]] = []
    for start in range(len(words) - width + 1):
        if all(words[start + offset].text == token for offset, token in enumerate(tokens)):
            runs.append(words[start : start + width])
    return runs


def verify_citation(path: str | Path, citation: Citation, value: str) -> None:
    DocumentText.load(path).verify(citation, value)


__all__ = [
    "CitationError",
    "DocumentText",
    "PageText",
    "Word",
    "normalize_space",
    "verify_citation",
]
