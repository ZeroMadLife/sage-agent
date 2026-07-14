"""Database model tests."""

import json

from sqlalchemy import select, text

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
            await connection.execute(text("SELECT revision FROM schema_migrations"))
        ).scalars().all()
        tables = set(
            (
                await connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            )
            .scalars()
            .all()
        )

    await engine.dispose()

    assert revisions == [
        "20260713_v7_cloud_control_plane",
        "20260713_v7_github_oauth",
        "20260714_v7_model_providers",
    ]
    assert {
        "cloud_model_providers",
        "cloud_models",
        "cloud_model_preferences",
    } <= tables
