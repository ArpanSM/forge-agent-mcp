from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from forge_agent_mcp.config import Settings
from forge_agent_mcp.providers import (
    AllProvidersFailed,
    ChatMessage,
    FakeProvider,
    FallbackProvider,
    OpenAICompatibleProvider,
    ProviderError,
    build_provider,
)


class ExtractResult(BaseModel):
    legal_name: str
    ein: str


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _completion_body(content: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-test",
        "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


def test_fake_provider_returns_configured_text_deterministically() -> None:
    provider = FakeProvider(text="stable answer")

    first = provider.complete([ChatMessage(role="user", content="hello")])
    second = provider.complete([ChatMessage(role="user", content="hello")])

    assert first.text == "stable answer"
    assert second.text == "stable answer"
    assert first == second
    assert provider.name == "fake"


def test_fake_provider_queues_structured_models_in_order() -> None:
    provider = FakeProvider()
    provider.queue_structured({"legal_name": "Ridgeline Haulage LLC", "ein": "00-0000001"})
    provider.queue_structured(
        ExtractResult(legal_name="Cedar & Pine Transport Inc.", ein="00-0000002")
    )

    first = provider.complete_structured(
        [ChatMessage(role="user", content="extract")], ExtractResult
    )
    second = provider.complete_structured(
        [ChatMessage(role="user", content="extract")], ExtractResult
    )

    assert first.legal_name == "Ridgeline Haulage LLC"
    assert second.ein == "00-0000002"
    assert [call.kind for call in provider.calls] == ["structured", "structured"]


def test_fake_provider_without_structured_response_raises() -> None:
    provider = FakeProvider()

    with pytest.raises(ProviderError, match="no structured response"):
        provider.complete_structured([ChatMessage(role="user", content="extract")], ExtractResult)


def test_fake_provider_rejects_structured_value_that_does_not_match_schema() -> None:
    provider = FakeProvider()
    provider.queue_structured({"legal_name": "Missing EIN"})

    with pytest.raises(ValidationError):
        provider.complete_structured([ChatMessage(role="user", content="extract")], ExtractResult)


def test_openai_compatible_provider_posts_chat_completion() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion_body("hello from the model"))

    provider = OpenAICompatibleProvider(
        name="orcarouter",
        base_url="https://api.example.test/v1/",
        model="test-model",
        api_key="test-key",
        reasoning_effort="none",
        http_client=_client(handler),
    )

    result = provider.complete(
        [
            ChatMessage(role="system", content="be terse"),
            ChatMessage(role="user", content="hello"),
        ],
        temperature=0.0,
    )

    assert result.text == "hello from the model"
    assert result.provider == "orcarouter"
    assert result.model == "test-model"
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://api.example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "test-model"
    assert payload["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
    ]
    assert payload["reasoning_effort"] == "none"


def test_openai_compatible_provider_parses_structured_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=_completion_body(
                '{"legal_name": "Ridgeline Haulage LLC", "ein": "00-0000001"}'
            ),
        )

    provider = OpenAICompatibleProvider(
        name="lfm3",
        base_url="http://127.0.0.1:8080/v1",
        model="lfm3-1.7b",
        api_key=None,
        require_api_key=False,
        http_client=_client(handler),
    )

    result = provider.complete_structured(
        [ChatMessage(role="user", content="extract the W-9")], ExtractResult
    )

    assert result == ExtractResult(legal_name="Ridgeline Haulage LLC", ein="00-0000001")
    payload = json.loads(seen[0].content)
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "ExtractResult"
    assert "required" in payload["response_format"]["json_schema"]["schema"]


def test_openai_compatible_provider_strips_markdown_fence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completion_body(
                '```json\n{"legal_name": "Ridgeline Haulage LLC", "ein": "00-0000001"}\n```'
            ),
        )

    provider = OpenAICompatibleProvider(
        name="lfm3",
        base_url="http://127.0.0.1:8080/v1",
        model="lfm3-1.7b",
        require_api_key=False,
        http_client=_client(handler),
    )

    result = provider.complete_structured(
        [ChatMessage(role="user", content="extract")], ExtractResult
    )

    assert result.ein == "00-0000001"


def test_openai_compatible_provider_raises_provider_error_on_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    provider = OpenAICompatibleProvider(
        name="orcarouter",
        base_url="https://api.example.test/v1",
        model="test-model",
        api_key="test-key",
        http_client=_client(handler),
    )

    with pytest.raises(ProviderError, match="HTTP 429"):
        provider.complete([ChatMessage(role="user", content="hello")])


def test_openai_compatible_provider_raises_provider_error_on_malformed_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_body("not json at all"))

    provider = OpenAICompatibleProvider(
        name="orcarouter",
        base_url="https://api.example.test/v1",
        model="test-model",
        api_key="test-key",
        http_client=_client(handler),
    )

    with pytest.raises(ProviderError, match="structured output"):
        provider.complete_structured([ChatMessage(role="user", content="extract")], ExtractResult)


def test_fallback_provider_uses_secondary_after_primary_failure() -> None:
    class Broken(FakeProvider):
        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            raise ProviderError("primary down")

    provider = FallbackProvider(primary=Broken(), fallback=FakeProvider(text="from fallback"))

    result = provider.complete([ChatMessage(role="user", content="hello")])

    assert result.text == "from fallback"
    assert result.provider == "fake"


def test_fallback_provider_reports_when_every_provider_fails() -> None:
    class Broken(FakeProvider):
        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            raise ProviderError("down")

    provider = FallbackProvider(primary=Broken(), fallback=Broken())

    with pytest.raises(AllProvidersFailed):
        provider.complete([ChatMessage(role="user", content="hello")])


def test_build_provider_defaults_to_fake_offline() -> None:
    provider = build_provider(Settings(model_provider="fake", _env_file=None))

    assert isinstance(provider, FakeProvider)
    assert provider.complete([ChatMessage(role="user", content="hello")]).text


def test_build_provider_requires_orcarouter_key() -> None:
    settings = Settings(
        model_provider="orcarouter",
        orcarouter_api_key=None,
        orcarouter_base_url="https://api.example.test/v1",
        _env_file=None,
    )

    with pytest.raises(ProviderError, match="ORCAROUTER_API_KEY"):
        build_provider(settings)


def test_settings_read_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "lfm3")
    monkeypatch.setenv("LFM3_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("LFM3_MODEL", "test-lfm3")

    settings = Settings(_env_file=None)

    assert settings.model_provider == "lfm3"
    assert settings.lfm3_base_url == "http://127.0.0.1:9999/v1"
    assert settings.lfm3_model == "test-lfm3"
