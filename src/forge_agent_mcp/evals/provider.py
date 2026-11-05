"""Provider wrapper that estimates and accumulates token usage.

The provider abstraction does not return usage metadata, so the eval harness
estimates tokens from characters and labels the result as an estimate. The
wrapper is deliberately transparent to the pipeline: it delegates every call.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..providers.base import (
    ChatMessage,
    ChatProvider,
    Completion,
    StructuredModel,
)


def estimate_tokens(text: str) -> int:
    """A stable, conservative character-based token estimate."""

    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


@dataclass(frozen=True)
class TokenSnapshot:
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class TokenCountingProvider:
    """Count estimated input/output tokens for every delegated call."""

    def __init__(self, delegate: ChatProvider) -> None:
        self._delegate = delegate
        self.name = delegate.name
        self.model = delegate.model
        self._input_tokens = 0
        self._output_tokens = 0

    def snapshot(self) -> TokenSnapshot:
        return TokenSnapshot(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )

    def _record_input(self, messages: Sequence[ChatMessage]) -> None:
        for message in messages:
            self._input_tokens += estimate_tokens(message.content)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        self._record_input(messages)
        completion = self._delegate.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._output_tokens += estimate_tokens(completion.text)
        return completion

    def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[StructuredModel],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> StructuredModel:
        self._record_input(messages)
        result = self._delegate.complete_structured(
            messages,
            schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._output_tokens += estimate_tokens(result.model_dump_json())
        return result


__all__ = ["TokenCountingProvider", "TokenSnapshot", "estimate_tokens"]
