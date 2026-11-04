from __future__ import annotations

from forge_agent_mcp.config import Settings


def test_library_settings_do_not_own_a_gateway_database_default(monkeypatch) -> None:
    monkeypatch.delenv("GATEWAY_DB_PATH", raising=False)

    settings = Settings(_env_file=None)

    assert settings.gateway_db_path is None


def test_approval_secret_has_no_default_and_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("FORGE_APPROVAL_SECRET", raising=False)
    assert Settings(_env_file=None).forge_approval_secret is None

    monkeypatch.setenv("FORGE_APPROVAL_SECRET", "explicit-console-secret")
    settings = Settings(_env_file=None)
    assert settings.forge_approval_secret is not None
    assert settings.forge_approval_secret.get_secret_value() == "explicit-console-secret"
