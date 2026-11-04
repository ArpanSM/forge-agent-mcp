"""OpenAI-compatible chat provider used for OrcaRouter and local LFM3."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from .base import (
    ChatMessage,
    Completion,
    ProviderError,
    ProviderUnavailable,
    StructuredModel,
    message_dicts,
)


def _truncate(value: str, limit: int = 300) -> str:
    value = value.strip()
    return value if len(value) <= limit else f"{value[:limit]}..."


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return ""


def _strip_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


class OpenAICompatibleProvider:
    """Minimal JSON-over-HTTP client for a ``/chat/completions`` endpoint."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        require_api_key: bool = True,
        timeout: float = 45.0,
        max_tokens: int = 2048,
        reasoning_effort: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.require_api_key = require_api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self._http_client = http_client

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        payload = self._payload(messages, temperature=temperature, max_tokens=max_tokens)
        body = self._post(payload)
        return Completion(
            text=self._extract_content(body),
            provider=self.name,
            model=str(body.get("model") or self.model),
        )

    def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[StructuredModel],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> StructuredModel:
        payload = self._payload(messages, temperature=temperature, max_tokens=max_tokens)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        }
        body = self._post(payload, retry_json_object_on_bad_request=True)
        text = _strip_fence(self._extract_content(body))
        try:
            return schema.model_validate_json(text)
        except ValueError as exc:
            raise ProviderError(
                f"{self.name}: structured output did not match {schema.__name__}: "
                f"{_truncate(str(exc))}"
            ) from exc

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": message_dicts(messages),
            "temperature": temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _post(
        self,
        payload: dict[str, Any],
        *,
        retry_json_object_on_bad_request: bool = False,
    ) -> dict[str, Any]:
        if self.require_api_key and not self.api_key:
            raise ProviderUnavailable(f"{self.name}: API key is not configured")

        response = self._request(payload)
        if (
            retry_json_object_on_bad_request
            and response.status_code == 400
            and payload.get("response_format", {}).get("type") == "json_schema"
        ):
            payload["response_format"] = {"type": "json_object"}
            response = self._request(payload)

        if response.status_code >= 400:
            raise ProviderError(
                f"{self.name}: HTTP {response.status_code}: {_truncate(response.text)}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name}: response was not JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError(f"{self.name}: response was not a JSON object")
        return body

    def _request(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(timeout=self.timeout)
        try:
            return client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                client.close()

    def _extract_content(self, body: dict[str, Any]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(f"{self.name}: response contained no choices")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = _content_text(message.get("content") if isinstance(message, dict) else None)
        if not content.strip():
            raise ProviderError(f"{self.name}: completion was empty")
        return content
