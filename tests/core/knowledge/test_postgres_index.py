from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg2
import pytest

from core.knowledge.postgres_index import (
    POSTGRES_INDEX_SCHEMA_REVISION,
    PostgresKnowledgeIndex,
    PostgresKnowledgeIndexConfig,
)
from core.knowledge.retrieval import HashingEmbeddingProvider
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_dsn() -> str:
    value = os.environ.get("SAGE_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("SAGE_TEST_POSTGRES_DSN is required for PostgreSQL integration tests")
    return value


@pytest.fixture
def postgres_store(tmp_path: Path, postgres_dsn: str) -> Iterator[KnowledgeStore]:
    source = tmp_path / "source"
    source.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    workspace_id = f"pg-test-{uuid.uuid4().hex}"
    index = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
        workspace_id=workspace_id,
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
    )
    store = KnowledgeStore(
        workspace,
        tmp_path / "canonical.sqlite3",
        {
            "official": KnowledgeSourceRoot(
                root_id="official",
                kind="markdown",
                label="Official",
                path=source,
            )
        },
        knowledge_index=index,
    )
    try:
        yield store
    finally:
        index.delete_workspace()
        index.close()


def test_postgres_schema_has_gin_and_no_ann_indexes(postgres_store: KnowledgeStore) -> None:
    postgres_store.initialize()
    index = postgres_store.knowledge_index
    assert isinstance(index, PostgresKnowledgeIndex)

    with psycopg2.connect(index.config.dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename LIKE 'knowledge_index_%'
            ORDER BY indexname
            """
        )
        indexes = tuple((str(row[0]), str(row[1])) for row in cursor.fetchall())
        cursor.execute("SELECT revision FROM knowledge_index_schema_migrations ORDER BY revision")
        revisions = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema()
              AND table_name='knowledge_index_source_revisions'
            """
        )
        revision_columns = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT COUNT(*) FROM pg_stat_activity
            WHERE application_name='sage-knowledge-index'
              AND state='idle in transaction'
            """
        )
        idle_in_transaction = int(cursor.fetchone()[0])

    assert any("USING gin (search_tsv)" in definition for _name, definition in indexes)
    assert all("hnsw" not in definition.casefold() for _name, definition in indexes)
    assert all("ivfflat" not in definition.casefold() for _name, definition in indexes)
    assert POSTGRES_INDEX_SCHEMA_REVISION in revisions
    assert "embedding_dimensions" in revision_columns
    assert idle_in_transaction == 0


def test_postgres_exact_routes_preserve_filters_revisions_and_citations(
    postgres_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "retrieval.md"
    source.write_text(
        "# Agent Retrieval\n\n旧版使用 context_schema 与 /api/v1/agent/run。\n",
        encoding="utf-8",
    )
    first = postgres_store.ingest("official", "retrieval.md")
    postgres_store.approve(first.proposal_id, first.revision)
    old_hit = postgres_store.search("context_schema", retrieval_mode="sparse")[0]

    source.write_text(
        "# Agent Retrieval\n\n新版使用 graph_state 与 /api/v2/agent/run。\n",
        encoding="utf-8",
    )
    second = postgres_store.ingest("official", "retrieval.md")
    postgres_store.approve(second.proposal_id, second.revision)

    sparse = postgres_store.search("graph_state /api/v2", retrieval_mode="sparse")
    dense = postgres_store.search("graph_state", retrieval_mode="dense")
    hybrid = postgres_store.search("graph_state", retrieval_mode="hybrid")
    stale_default = postgres_store.search("context_schema", retrieval_mode="sparse")
    historical = postgres_store.search(
        "context_schema",
        page_revisions=(old_hit.chunk.page_revision,),
        retrieval_mode="sparse",
    )

    assert sparse and sparse[0].sparse_rank == 1 and sparse[0].dense_rank is None
    assert dense and dense[0].dense_rank == 1 and dense[0].sparse_rank is None
    assert hybrid and hybrid[0].sparse_rank == 1 and hybrid[0].dense_rank == 1
    assert all(hit.chunk.source_id == second.source_id for hit in sparse)
    assert (
        postgres_store.search("graph_state", source_ids=("unknown",), retrieval_mode="sparse") == ()
    )
    assert postgres_store.search("graph_state", visibility="public", retrieval_mode="sparse") == ()
    assert stale_default == ()
    assert historical[0].chunk.page_revision == old_hit.chunk.page_revision
    assert historical[0].chunk.active is False
    assert postgres_store.citation(sparse[0].citation_id).chunk_id == sparse[0].chunk.chunk_id
    with pytest.raises(KeyError, match="stale or unknown"):
        postgres_store.citation(old_hit.citation_id)


def test_postgres_force_rebuild_is_idempotent_and_preserves_stable_ids(
    postgres_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    (tmp_path / "source" / "langgraph.md").write_text(
        "# LangGraph\n\nCommand supports goto and update fields.\n",
        encoding="utf-8",
    )
    proposal = postgres_store.ingest("official", "langgraph.md")
    postgres_store.approve(proposal.proposal_id, proposal.revision)
    before = postgres_store.search("Command goto", retrieval_mode="sparse")[0]

    first = postgres_store.rebuild_index()
    second = postgres_store.rebuild_index()
    after = postgres_store.search("Command goto", retrieval_mode="sparse")[0]

    assert first.error_count == second.error_count == 0
    assert first.indexed_revision_count == second.indexed_revision_count == 1
    assert first.active_chunk_count == second.active_chunk_count == 1
    assert after.chunk.chunk_id == before.chunk.chunk_id
    assert after.citation_id == before.citation_id
    assert after.chunk.page_revision == before.chunk.page_revision


def test_postgres_dimension_change_rebuilds_matching_provider_revision(
    postgres_store: KnowledgeStore,
    postgres_dsn: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "source" / "dimensions.md").write_text(
        "# Dimensions\n\nEmbedding dimensions are part of the projection contract.\n",
        encoding="utf-8",
    )
    proposal = postgres_store.ingest("official", "dimensions.md")
    postgres_store.approve(proposal.proposal_id, proposal.revision)
    original = postgres_store.knowledge_index
    assert isinstance(original, PostgresKnowledgeIndex)
    resized = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
        workspace_id=original.workspace_id,
        embedding_provider=HashingEmbeddingProvider(dimensions=32),
    )
    resized_store = KnowledgeStore(
        postgres_store.workspace_root,
        postgres_store.database_path,
        postgres_store.source_roots,
        knowledge_index=resized,
    )
    try:
        summary = resized_store.index_summary()
        assert summary.error_count == 0
        assert resized_store.search("projection contract", retrieval_mode="dense")
        with psycopg2.connect(postgres_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT embedding_dimensions FROM knowledge_index_chunks
                WHERE workspace_id=%s
                """,
                (original.workspace_id,),
            )
            assert cursor.fetchall() == [(32,)]
    finally:
        resized.close()


def test_postgres_failed_revision_is_atomic_and_rebuildable(
    tmp_path: Path,
    postgres_dsn: str,
) -> None:
    class FailingProvider(HashingEmbeddingProvider):
        def embed(self, text: str) -> tuple[float, ...]:
            if "FAIL_INDEX" in text:
                raise RuntimeError("synthetic projection failure")
            return super().embed(text)

    source = tmp_path / "source"
    source.mkdir()
    (source / "failure.md").write_text(
        "# Failure\n\n第一段可索引。\n\nFAIL_INDEX 触发失败。\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    workspace_id = f"pg-failure-{uuid.uuid4().hex}"
    failing = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
        workspace_id=workspace_id,
        embedding_provider=FailingProvider(dimensions=64),
    )
    store = KnowledgeStore(
        workspace,
        tmp_path / "canonical.sqlite3",
        {
            "official": KnowledgeSourceRoot(
                root_id="official",
                kind="markdown",
                label="Official",
                path=source,
            )
        },
        knowledge_index=failing,
    )
    try:
        proposal = store.ingest("official", "failure.md")
        store.approve(proposal.proposal_id, proposal.revision)
        failed = store.index_summary()

        assert failed.error_count == 1
        assert failed.total_chunk_count == 0

        recovered = PostgresKnowledgeIndex(
            PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
            workspace_id=workspace_id,
            embedding_provider=HashingEmbeddingProvider(dimensions=64),
        )
        store.knowledge_index = recovered
        rebuilt = store.rebuild_index()

        assert rebuilt.error_count == 0
        assert rebuilt.active_chunk_count == 2
        assert store.search("触发失败", retrieval_mode="sparse")
        recovered.close()
    finally:
        failing.delete_workspace()
        failing.close()
