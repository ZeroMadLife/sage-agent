"""Database model tests."""

import json

from sqlalchemy import inspect, select, text

from db.database import create_engine, create_session_factory
from db.migrations import init_db
from db.models import ItineraryRecord, MessageRecord, SessionRecord


async def test_session_message_and_itinerary_crud() -> None:
    """Session, message, and itinerary records should persist via async SQLAlchemy."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = create_session_factory(engine)
    await init_db(engine)

    async with session_factory() as session:
        session.add(SessionRecord(id="session-001", user_id="u_1", title="杭州2日游"))
        session.add(
            MessageRecord(
                session_id="session-001",
                role="user",
                content="帮我规划杭州2日游",
                tool_calls_json=None,
            )
        )
        session.add(
            ItineraryRecord(
                session_id="session-001",
                user_id="u_1",
                destination="杭州",
                content_json=json.dumps({"destination": "杭州"}, ensure_ascii=False),
                total_cost=200,
            )
        )
        await session.commit()

    async with session_factory() as session:
        stored_session = await session.get(SessionRecord, "session-001")
        messages = (
            await session.scalars(
                select(MessageRecord).where(MessageRecord.session_id == "session-001")
            )
        ).all()
        itineraries = (
            await session.scalars(select(ItineraryRecord).where(ItineraryRecord.user_id == "u_1"))
        ).all()

    await engine.dispose()

    assert stored_session is not None
    assert stored_session.user_id == "u_1"
    assert stored_session.title == "杭州2日游"
    assert len(messages) == 1
    assert messages[0].content == "帮我规划杭州2日游"
    assert len(itineraries) == 1
    assert itineraries[0].destination == "杭州"
    assert itineraries[0].total_cost == 200


async def test_session_record_defaults() -> None:
    """Session records should have active status and timestamps by default."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = create_session_factory(engine)
    await init_db(engine)

    async with session_factory() as session:
        session.add(SessionRecord(id="session-002", user_id="u_2"))
        await session.commit()

    async with session_factory() as session:
        stored_session = await session.get(SessionRecord, "session-002")

    await engine.dispose()

    assert stored_session is not None
    assert stored_session.title == ""
    assert stored_session.status == "active"
    assert stored_session.created_at is not None
    assert stored_session.updated_at is not None


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

    await engine.dispose()

    assert revisions == [
        "20260713_v7_cloud_control_plane",
        "20260713_v7_github_oauth",
        "20260714_v7_model_providers",
        "20260715_v7_2_knowledge_jobs",
        "20260716_v7_5_3_knowledge_sync",
        "20260716_v7_5_4_source_connectors",
    ]
    assert {
        "cloud_model_providers",
        "cloud_models",
        "cloud_model_preferences",
        "knowledge_workspaces",
        "knowledge_source_roots",
        "knowledge_ingest_jobs",
        "knowledge_ingest_items",
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
            lambda sync_connection: inspect(sync_connection).get_indexes(
                "knowledge_ingest_jobs"
            )
        )

    await engine.dispose()

    assert "sync_plan_id" in job_columns
    assert "change_kind" in item_columns
    assert any(
        index["name"] == "knowledge_job_sync_plan_key" and index["unique"]
        for index in indexes
    )
