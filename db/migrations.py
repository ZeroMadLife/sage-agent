"""Small, versioned schema migration entry point for the V7 control plane."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from db.database import engine as default_engine
from db.models import Base


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create base tables and record the idempotent V7 schema revision."""
    target = engine or default_engine
    async with target.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
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
