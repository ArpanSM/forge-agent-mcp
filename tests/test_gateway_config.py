from __future__ import annotations

from forge_agent_mcp.config import Settings


def test_library_settings_do_not_own_a_gateway_database_default(monkeypatch) -> None:
    monkeypatch.delenv("GATEWAY_DB_PATH", raising=False)

    settings = Settings(_env_file=None)

    assert settings.gateway_db_path is None
