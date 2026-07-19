"""Local database bootstrap and startup cleanup contracts."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import create_app
from core.config.settings import get_settings


def test_development_startup_runs_idempotent_database_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_init_db() -> None:
        calls.append("migrate")

    monkeypatch.setattr(api_main, "init_db", fake_init_db)
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    app = create_app(
        coding_deerflow_v2_enabled=False,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

    assert calls == ["migrate"]
    get_settings.cache_clear()


def test_production_startup_never_runs_implicit_database_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_init_db() -> None:
        calls.append("migrate")

    monkeypatch.setattr(api_main, "init_db", fake_init_db)
    app = create_app(
        cloud_app_env="production",
        cloud_repository=object(),
        database_auto_migrate=True,
        coding_deerflow_v2_enabled=False,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

    assert calls == []


def test_failed_local_migration_does_not_open_harness_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fail_init_db() -> None:
        raise RuntimeError("missing database")

    monkeypatch.setattr(api_main, "init_db", fail_init_db)

    def fail_if_opened(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("checkpointer must not open before database migration")

    monkeypatch.setattr(api_main, "open_sqlite_checkpointer", fail_if_opened)
    app = create_app(
        database_auto_migrate=True,
        coding_deerflow_v2_enabled=True,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )

    with pytest.raises(RuntimeError, match="local database migration failed"), TestClient(app):
        pass
