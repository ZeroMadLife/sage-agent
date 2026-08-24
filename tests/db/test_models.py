"""Database model tests."""

from sqlalchemy import inspect, text

from db.database import create_engine
from db.migrations import init_db
from db.models import Base


async def test_init_db_records_the_v7_cloud_control_plane_revision() -> None:
    """Existing deployments can run an idempotent, observable V7 schema upgrade."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    await init_db(engine)

    async with engine.connect() as connection:
        revisions = (
            (await connection.execute(text("SELECT revision FROM schema_migrations")))
            .scalars()
            .all()
        )
        tables = set(
            (await connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")))
            .scalars()
            .all()
        )
        sync_columns = await connection.run_sync(
            lambda sync_connection: {
                str(column["name"])
                for column in inspect(sync_connection).get_columns("knowledge_source_sync")
            }
        )
        plan_columns = await connection.run_sync(
            lambda sync_connection: {
                str(column["name"])
                for column in inspect(sync_connection).get_columns("knowledge_sync_plans")
            }
        )
        refresh_indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes("cloud_refresh_tokens")
        )

    await engine.dispose()

    assert set(revisions) == {
        "20260713_v7_cloud_control_plane",
        "20260713_v7_github_oauth",
        "20260714_v7_model_providers",
        "20260715_v7_2_knowledge_jobs",
        "20260716_v7_5_3_knowledge_sync",
        "20260716_v7_5_4_source_connectors",
        "20260718_h2_5b2_external_parse_tasks",
        "20260718_v7_canary_invite_device_login",
        "20260718_h2_5c_knowledge_source_proposals",
        "20260723_public_publication_candidates",
        "20260823_v8_rotating_client_tokens",
    }
    assert {
        "cloud_model_providers",
        "cloud_models",
        "cloud_model_preferences",
        "knowledge_workspaces",
        "knowledge_source_roots",
        "knowledge_ingest_jobs",
        "knowledge_ingest_items",
        "knowledge_external_parse_tasks",
        "knowledge_source_proposals",
        "knowledge_source_proposal_events",
        "public_publication_candidates",
        "public_publication_candidate_events",
        "cloud_refresh_tokens",
        "knowledge_ingest_idempotency",
        "knowledge_job_events",
        "knowledge_source_manifests",
        "knowledge_source_sync",
        "knowledge_sync_plans",
    } <= tables
    assert {
        "adapter_id",
        "adapter_version",
        "adapter_checkpoint",
        "resume_cursor",
        "scan_status",
        "last_error_code",
        "last_error_message",
        "last_scan_started_at",
        "last_scan_completed_at",
    } <= sync_columns
    assert {
        "adapter_id",
        "adapter_version",
        "base_checkpoint",
        "target_checkpoint",
    } <= plan_columns
    refresh_index_names = {str(index["name"]) for index in refresh_indexes}
    assert {
        "ix_cloud_refresh_tokens_expires_at",
        "ix_cloud_refresh_tokens_family_id",
        "ix_cloud_refresh_tokens_token_hash",
        "ix_cloud_refresh_tokens_user_id",
    } <= refresh_index_names
    token_hash_index = next(
        index for index in refresh_indexes if index["name"] == "ix_cloud_refresh_tokens_token_hash"
    )
    assert bool(token_hash_index["unique"]) is True


async def test_init_db_adds_refresh_tokens_to_a_pre_token_schema() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    legacy_tables = [
        table for table in Base.metadata.sorted_tables if table.name != "cloud_refresh_tokens"
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=legacy_tables,
            )
        )

    await init_db(engine)
    await init_db(engine)

    async with engine.connect() as connection:
        tables = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
        revisions = set(
            (await connection.execute(text("SELECT revision FROM schema_migrations")))
            .scalars()
            .all()
        )

    await engine.dispose()

    assert "cloud_refresh_tokens" in tables
    assert "20260823_v8_rotating_client_tokens" in revisions


async def test_init_db_upgrades_legacy_knowledge_job_tables_in_place() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(
            text("CREATE TABLE knowledge_ingest_jobs (id VARCHAR(36) PRIMARY KEY)")
        )
        await connection.execute(
            text("CREATE TABLE knowledge_ingest_items (id VARCHAR(36) PRIMARY KEY)")
        )

    await init_db(engine)
    await init_db(engine)

    async with engine.connect() as connection:
        job_columns = await connection.run_sync(
            lambda sync_connection: {
                str(column["name"])
                for column in inspect(sync_connection).get_columns("knowledge_ingest_jobs")
            }
        )
        item_columns = await connection.run_sync(
            lambda sync_connection: {
                str(column["name"])
                for column in inspect(sync_connection).get_columns("knowledge_ingest_items")
            }
        )
        indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes("knowledge_ingest_jobs")
        )

    await engine.dispose()

    assert "sync_plan_id" in job_columns
    assert "change_kind" in item_columns
    assert any(
        index["name"] == "knowledge_job_sync_plan_key" and index["unique"] for index in indexes
    )
