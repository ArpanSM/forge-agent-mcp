"""Fallback chain and provider selection."""

from __future__ import annotations

from collections.abc import Sequence

from ..config import Settings, _secret
from .base import (
    AllProvidersFailed,
    ChatMessage,
    ChatProvider,
    Completion,
    ProviderError,
    ProviderUnavailable,
    StructuredModel,
)
from .fake import FakeProvider
from .openai_compat import OpenAICompatibleProvider


class FallbackProvider:
    """Tries the primary provider, then the fallback on any provider failure."""

    name = "fallback"

    def __init__(self, *, primary: ChatProvider, fallback: ChatProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.model = primary.model

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        errors: list[str] = []
        for provider in (self.primary, self.fallback):
            try:
                return provider.complete(messages, temperature=temperature, max_tokens=max_tokens)
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
        raise AllProvidersFailed("all providers failed: " + "; ".join(errors))

    def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[StructuredModel],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> StructuredModel:
        errors: list[str] = []
        for provider in (self.primary, self.fallback):
            try:
                return provider.complete_structured(
                    messages, schema, temperature=temperature, max_tokens=max_tokens
                )
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
        raise AllProvidersFailed("all providers failed: " + "; ".join(errors))


def _orcarouter(settings: Settings) -> ChatProvider:
    api_key = _secret(settings.orcarouter_api_key)
    if not api_key:
        raise ProviderUnavailable("ORCAROUTER_API_KEY is not configured")
    return OpenAICompatibleProvider(
        name="orcarouter",
        base_url=settings.orcarouter_base_url,
        model=settings.orcarouter_model,
        api_key=api_key,
        require_api_key=True,
        timeout=180.0,
        reasoning_effort=settings.orcarouter_reasoning_effort,
    )


def _lfm3(settings: Settings) -> ChatProvider:
    return OpenAICompatibleProvider(
        name="lfm3",
        base_url=settings.lfm3_base_url,
        model=settings.lfm3_model,
        api_key=_secret(settings.lfm3_api_key),
        require_api_key=False,
        timeout=60.0,
    )


def build_provider(settings: Settings | None = None) -> ChatProvider:
    """Build the configured provider; OrcaRouter gets the local LFM3 fallback."""

    settings = settings or Settings()
    if settings.model_provider == "fake":
        return FakeProvider()
    if settings.model_provider == "lfm3":
        return _lfm3(settings)
    return FallbackProvider(primary=_orcarouter(settings), fallback=_lfm3(settings))
