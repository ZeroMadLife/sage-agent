"""Build or rebuild the PostgreSQL Knowledge search projection from SQLite truth."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict

from core.config.settings import get_settings
from core.knowledge.embedding_factory import build_knowledge_embedding_provider
from core.knowledge.postgres_index import (
    POSTGRES_INDEX_SCHEMA_REVISION,
    PostgresKnowledgeIndex,
    PostgresKnowledgeIndexConfig,
)
from core.knowledge.store import KnowledgeStore


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=settings.knowledge_workspace_root)
    parser.add_argument("--database", default=settings.knowledge_database_path)
    parser.add_argument("--workspace-id", default=settings.knowledge_workspace_id)
    parser.add_argument(
        "--postgres-dsn",
        default=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
        help="Defaults to KNOWLEDGE_POSTGRES_DSN or the POSTGRES_* settings.",
    )
    parser.add_argument("--dimensions", type=int, default=256)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not str(args.workspace).strip() or not str(args.database).strip():
        parser.error("--workspace and --database are required")

    index = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(
            dsn=args.postgres_dsn,
            connect_timeout_seconds=settings.knowledge_postgres_connect_timeout_seconds,
            pool_max_connections=settings.knowledge_postgres_pool_max_connections,
        ),
        workspace_id=args.workspace_id,
        embedding_provider=build_knowledge_embedding_provider(
            settings,
            hashing_dimensions=args.dimensions,
        ),
    )
    started = time.perf_counter()
    try:
        if args.force:
            index.ensure_postgres_schema()
            index.delete_workspace()
        store = KnowledgeStore(args.workspace, args.database, {}, knowledge_index=index)
        store.initialize()
        summary = store.index_summary()
        result = {
            "schema_version": 1,
            "backend_schema_revision": POSTGRES_INDEX_SCHEMA_REVISION,
            "force_rebuild": bool(args.force),
            "elapsed_ms": round((time.perf_counter() - started) * 1_000, 3),
            "index": asdict(summary),
            "storage": index.storage_summary(),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if summary.error_count else 0
    finally:
        index.close()


if __name__ == "__main__":
    raise SystemExit(main())
