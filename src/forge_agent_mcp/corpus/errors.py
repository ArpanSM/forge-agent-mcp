"""Domain exceptions for corpus generation."""

from __future__ import annotations


class CorpusError(RuntimeError):
    """The corpus could not be generated or its labels are inconsistent."""
