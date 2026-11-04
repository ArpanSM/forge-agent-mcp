"""Configuration loaded from environment variables and an optional .env file."""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ORCAROUTER_BASE_URL = "https://api.orcarouter.ai/v1"
ORCAROUTER_DEFAULT_MODEL = "tencent/hy4-preview-free"
LFM3_BASE_URL = "http://127.0.0.1:8080/v1"
LFM3_DEFAULT_MODEL = "lfm3-1.7b"


class Settings(BaseSettings):
    """Runtime settings.

    Tests pass ``_env_file=None`` and explicit values so they never depend on the
    developer's local ``.env``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    model_provider: Literal["orcarouter", "lfm3", "fake"] = "fake"

    orcarouter_base_url: str = ORCAROUTER_BASE_URL
    orcarouter_api_key: SecretStr | None = None
    orcarouter_model: str = ORCAROUTER_DEFAULT_MODEL
    orcarouter_reasoning_effort: str | None = "none"

    lfm3_base_url: str = LFM3_BASE_URL
    lfm3_api_key: SecretStr | None = None
    lfm3_model: str = LFM3_DEFAULT_MODEL

    # MCP gateway. These defaults are for the local synthetic demo only; a
    # deployment must override the secret and use HTTPS.
    gateway_issuer: str = "http://127.0.0.1:8765"
    gateway_audience: str = "forge-freight-gateway"
    gateway_jwt_secret: SecretStr = SecretStr("forge-local-dev-jwt-secret-at-least-32-bytes")
    gateway_db_path: str | None = None
    gateway_tenant_id: str = "tenant_northwind"
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8765
    gateway_console_issuer: str = "forge-console"
    forge_approval_secret: SecretStr | None = None
    gateway_approval_ttl_seconds: int = 900


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value()
    return text or None
