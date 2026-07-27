"""Pytest global fixtures."""

from collections.abc import Iterator

import pytest
from _pytest.monkeypatch import MonkeyPatch

from core.config.settings import get_settings


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch: MonkeyPatch) -> Iterator[None]:
    """Inject test environment variables for all tests."""
    test_env = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "test",
        "POSTGRES_PASSWORD": "test",
        "POSTGRES_DB": "test",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "LLM_MODEL": "doubao:Doubao-Seed-2.0-pro",
        "LLM_LIGHT_MODEL": "deepseek:deepseek-v4-flash",
        "APP_ENV": "test",
        # Most existing tests exercise the compatibility runtime explicitly.
        # Rollout/default-profile tests opt in at the app-factory boundary.
        "SAGE_DEERFLOW_V2_ENABLED": "false",
        "SAGE_CODING_DEFAULT_RUNTIME_PROFILE": "deerflow_v2",
        # Developer-local Knowledge Workspace settings must never make API
        # tests read or mutate a real vault/repository.
        "KNOWLEDGE_WORKSPACE_ROOT": "",
        "KNOWLEDGE_DATABASE_PATH": "",
        "KNOWLEDGE_SOURCE_ROOT": "",
        "KNOWLEDGE_JOBS_ENABLED": "false",
        "KNOWLEDGE_EXTERNAL_PARSING_ENABLED": "false",
        "KNOWLEDGE_EXTERNAL_ALLOWED_SOURCE_IDS": "",
        "KNOWLEDGE_QWEN_VL_ENABLED": "false",
        "KNOWLEDGE_QWEN_VL_API_KEY": "",
        "KNOWLEDGE_RETRIEVAL_OBSERVABILITY_ENABLED": "false",
        "KNOWLEDGE_RETRIEVAL_OBSERVABILITY_HMAC_KEY": "",
        "KNOWLEDGE_RETRIEVAL_OBSERVABILITY_CANDIDATE_LIMIT": "50",
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
