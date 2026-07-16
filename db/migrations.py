"""Small, versioned schema migration entry point for the V7 control plane."""

import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from db.database import engine as default_engine
from db.models import Base


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create base tables and record the idempotent V7 schema revision."""
    target = engine or default_engine
    async with target.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await _upgrade_knowledge_sync_columns(connection)
        await _upgrade_knowledge_source_adapter_columns(connection)
        await connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(revision VARCHAR(100) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (revision, applied_at) "
                "SELECT '20260713_v7_cloud_control_plane', CURRENT_TIMESTAMP "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM schema_migrations "
                "WHERE revision = '20260713_v7_cloud_control_plane'"
                ")"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (revision, applied_at) "
                "SELECT '20260713_v7_github_oauth', CURRENT_TIMESTAMP "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM schema_migrations "
                "WHERE revision = '20260713_v7_github_oauth'"
                ")"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (revision, applied_at) "
                "SELECT '20260714_v7_model_providers', CURRENT_TIMESTAMP "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM schema_migrations "
                "WHERE revision = '20260714_v7_model_providers'"
                ")"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (revision, applied_at) "
                "SELECT '20260715_v7_2_knowledge_jobs', CURRENT_TIMESTAMP "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM schema_migrations "
                "WHERE revision = '20260715_v7_2_knowledge_jobs'"
                ")"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (revision, applied_at) "
                "SELECT '20260716_v7_5_3_knowledge_sync', CURRENT_TIMESTAMP "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM schema_migrations "
                "WHERE revision = '20260716_v7_5_3_knowledge_sync'"
                ")"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (revision, applied_at) "
                "SELECT '20260716_v7_5_4_source_connectors', CURRENT_TIMESTAMP "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM schema_migrations "
                "WHERE revision = '20260716_v7_5_4_source_connectors'"
                ")"
            )
        )


async def _upgrade_knowledge_sync_columns(connection: AsyncConnection) -> None:
    """Add V7.5.3 columns to databases created before source manifests existed."""

    job_columns = await connection.run_sync(
        lambda sync_connection: {
            str(column["name"])
            for column in inspect(sync_connection).get_columns("knowledge_ingest_jobs")
        }
    )
    if "sync_plan_id" not in job_columns:
        await connection.execute(
            text("ALTER TABLE knowledge_ingest_jobs ADD COLUMN sync_plan_id VARCHAR(36)")
        )
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS knowledge_job_sync_plan_key "
                "ON knowledge_ingest_jobs (sync_plan_id)"
            )
        )

    item_columns = await connection.run_sync(
        lambda sync_connection: {
            str(column["name"])
            for column in inspect(sync_connection).get_columns("knowledge_ingest_items")
        }
    )
    if "change_kind" not in item_columns:
        await connection.execute(
            text(
                "ALTER TABLE knowledge_ingest_items "
                "ADD COLUMN change_kind VARCHAR(24) NOT NULL DEFAULT 'added'"
            )
        )


async def _upgrade_knowledge_source_adapter_columns(connection: AsyncConnection) -> None:
    """Add connector binding and checkpoint state to V7.5.3 databases."""

    sync_columns = await connection.run_sync(
        lambda sync_connection: {
            str(column["name"])
            for column in inspect(sync_connection).get_columns("knowledge_source_sync")
        }
    )
    sync_definitions = {
        "adapter_id": "VARCHAR(64) NOT NULL DEFAULT ''",
        "adapter_version": "VARCHAR(64) NOT NULL DEFAULT ''",
        "adapter_checkpoint": "VARCHAR(512)",
        "resume_cursor": "VARCHAR(2048)",
        "scan_status": "VARCHAR(32) NOT NULL DEFAULT 'idle'",
        "last_error_code": "VARCHAR(64)",
        "last_error_message": "VARCHAR(1000)",
        "last_scan_started_at": "TIMESTAMP",
        "last_scan_completed_at": "TIMESTAMP",
    }
    for column, definition in sync_definitions.items():
        if column not in sync_columns:
            await connection.execute(
                text(f"ALTER TABLE knowledge_source_sync ADD COLUMN {column} {definition}")
            )

    plan_columns = await connection.run_sync(
        lambda sync_connection: {
            str(column["name"])
            for column in inspect(sync_connection).get_columns("knowledge_sync_plans")
        }
    )
    plan_definitions = {
        "adapter_id": "VARCHAR(64) NOT NULL DEFAULT ''",
        "adapter_version": "VARCHAR(64) NOT NULL DEFAULT ''",
        "base_checkpoint": "VARCHAR(512)",
        "target_checkpoint": "VARCHAR(512)",
    }
    for column, definition in plan_definitions.items():
        if column not in plan_columns:
            await connection.execute(
                text(f"ALTER TABLE knowledge_sync_plans ADD COLUMN {column} {definition}")
            )


if __name__ == "__main__":
    asyncio.run(init_db())
