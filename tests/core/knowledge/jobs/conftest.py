"""Fixtures for durable knowledge job tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio

from core.knowledge import KnowledgeSourceRoot, KnowledgeStore
from core.knowledge.jobs import KnowledgeJobRepository, RedisKnowledgeJobQueue
from db.database import create_engine, create_session_factory
from db.migrations import init_db


@pytest.fixture
def knowledge_store(tmp_path: Path) -> tuple[KnowledgeStore, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    workspace = tmp_path / "knowledge"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    store = KnowledgeStore(
        workspace,
        workspace / ".sage" / "knowledge.sqlite3",
        {"vault": KnowledgeSourceRoot(root_id="vault", kind="obsidian", label="Vault", path=vault)},
    )
    store.initialize()
    return store, vault


@pytest_asyncio.fixture
async def job_infrastructure(tmp_path: Path) -> Any:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite3'}")
    await init_db(engine)
    repository = KnowledgeJobRepository(create_session_factory(engine))
    redis = fakeredis.aioredis.FakeRedis()
    queue = RedisKnowledgeJobQueue(redis, stream=f"test:{tmp_path.name}")
    yield repository, queue, redis
    await redis.aclose()
    await engine.dispose()
