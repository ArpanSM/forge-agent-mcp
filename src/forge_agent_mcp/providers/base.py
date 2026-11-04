"""Provider protocol, messages and completion types."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


class ProviderError(RuntimeError):
    """A provider could not produce a valid completion."""


class ProviderUnavailable(ProviderError):
    """A provider is not configured (for example, its API key is missing)."""


class ProviderTransportError(ProviderError):
    """A provider is unavailable, timed out or returned a non-JSON response."""


class ProviderSchemaError(ProviderError):
    """The provider returned JSON that did not match the requested schema.

    Extraction retries a schema failure once; a second failure becomes
    ``needs_more_info``. It must never trigger a silent provider fallback.
    """


class AllProvidersFailed(ProviderError):
    """Every provider in a fallback chain failed with a transport error."""


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    provider: str
    model: str


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


@runtime_checkable
class ChatProvider(Protocol):
    """The model seam used by extraction and explanation code."""

    name: str
    model: str

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion: ...

    def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[StructuredModel],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> StructuredModel: ...


def message_dicts(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages]
