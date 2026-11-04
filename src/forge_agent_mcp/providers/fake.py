"""Deterministic provider for tests and offline runs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from .base import ChatMessage, Completion, ProviderError, ProviderSchemaError, StructuredModel


@dataclass(frozen=True)
class ProviderCall:
    kind: Literal["chat", "structured"]
    messages: tuple[ChatMessage, ...]
    temperature: float
    schema_name: str | None = None


StructuredValue = BaseModel | dict[str, Any]


class FakeProvider:
    """Returns configured responses and records every call.

    With no queued response, ``complete`` returns ``default_text`` (or a stable
    placeholder). ``complete_structured`` requires a queued or default value so a
    test can never accidentally depend on generated content.
    """

    name = "fake"

    def __init__(
        self,
        *,
        model: str = "fake-model",
        text: str = "fake completion",
        structured: StructuredValue | None = None,
    ) -> None:
        self.model = model
        self._default_text = text
        self._default_structured = structured
        self._text_queue: list[str] = []
        self._structured_queue: list[StructuredValue] = []
        self.calls: list[ProviderCall] = []

    def queue_text(self, text: str) -> None:
        self._text_queue.append(text)

    def queue_structured(self, value: StructuredValue) -> None:
        self._structured_queue.append(value)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        del max_tokens
        self.calls.append(
            ProviderCall(kind="chat", messages=tuple(messages), temperature=temperature)
        )
        text = self._text_queue.pop(0) if self._text_queue else self._default_text
        return Completion(text=text, provider=self.name, model=self.model)

    def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[StructuredModel],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> StructuredModel:
        del max_tokens
        self.calls.append(
            ProviderCall(
                kind="structured",
                messages=tuple(messages),
                temperature=temperature,
                schema_name=schema.__name__,
            )
        )
        if self._structured_queue:
            value = self._structured_queue.pop(0)
        elif self._default_structured is not None:
            value = self._default_structured
        else:
            raise ProviderError("FakeProvider has no structured response queued")

        if isinstance(value, BaseModel):
            if isinstance(value, schema):
                return value
            value = value.model_dump()
        try:
            return schema.model_validate(value)
        except ValidationError as exc:
            raise ProviderSchemaError(
                f"FakeProvider structured response did not match {schema.__name__}"
            ) from exc
