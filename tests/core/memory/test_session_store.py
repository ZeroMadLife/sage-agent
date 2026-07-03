"""SessionStore tests with fakeredis and SQLite in-memory."""

import json
from typing import Any

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from core.memory.session_store import SessionStore
from db.database import create_engine, create_session_factory
from db.migrations import init_db
from db.models import ItineraryRecord, MessageRecord, SessionRecord
from models.itinerary import Itinerary


class FakeCompressor:
    """Small compressor test double."""

    def __init__(self, should_compress: bool = True) -> None:
        self.should_compress_value = should_compress
        self.seen_messages: list[dict[str, str]] = []

    def should_compress(self, messages: list[dict[str, str]]) -> bool:
        """Return the configured compression decision."""
        self.seen_messages = messages
        return self.should_compress_value

    async def compress(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """Return a deterministic compressed history."""
        return [
            {"role": "system", "content": "[对话摘要] 旧消息"},
            *messages[-2:],
        ]


@pytest.fixture
async def store_context() -> Any:
    """Create isolated Redis and SQLite stores."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    session_factory = create_session_factory(engine)
    redis = fakeredis.aioredis.FakeRedis()
    store = SessionStore(redis, session_factory)
    yield store, redis, session_factory
    await redis.aclose()
    await engine.dispose()


async def test_create_session_writes_pg(store_context: Any) -> None:
    """Creating a session persists it in PostgreSQL-compatible storage."""
    store, _, session_factory = store_context

    session_id = await store.create_session("u_1", title="杭州2日游")

    async with session_factory() as session:
        record = await session.get(SessionRecord, session_id)

    assert record is not None
    assert record.user_id == "u_1"
    assert record.title == "杭州2日游"


async def test_save_message_writes_redis_and_pg(store_context: Any) -> None:
    """Messages should be written to both Redis hot cache and PG history."""
    store, redis, session_factory = store_context
    session_id = await store.create_session("u_1")

    await store.save_message(
        session_id,
        role="assistant",
        content="你好",
        tool_calls=[{"tool": "get_weather", "error": ""}],
    )

    raw = await redis.get(f"session:{session_id}:messages")
    assert raw is not None
    assert json.loads(raw)["messages"] == [{"role": "assistant", "content": "你好"}]

    async with session_factory() as session:
        messages = (await session.scalars(select(MessageRecord))).all()

    assert len(messages) == 1
    assert messages[0].content == "你好"
    assert messages[0].tool_calls_json == '[{"tool": "get_weather", "error": ""}]'


async def test_load_messages_from_redis(store_context: Any) -> None:
    """Redis hit should avoid PG and return cached messages."""
    store, redis, _ = store_context
    session_id = await store.create_session("u_1")
    await redis.set(
        f"session:{session_id}:messages",
        json.dumps({"messages": [{"role": "user", "content": "Redis消息"}]}),
    )

    messages = await store.load_messages(session_id)

    assert messages == [{"role": "user", "content": "Redis消息"}]


async def test_load_messages_fallback_to_pg_and_backfills_redis(store_context: Any) -> None:
    """Redis miss should restore messages from PG and repopulate Redis."""
    store, redis, _ = store_context
    session_id = await store.create_session("u_1")
    await store.save_message(session_id, "user", "PG消息")
    await redis.delete(f"session:{session_id}:messages")

    messages = await store.load_messages(session_id)
    raw = await redis.get(f"session:{session_id}:messages")

    assert messages == [{"role": "user", "content": "PG消息"}]
    assert raw is not None
    assert json.loads(raw)["messages"] == messages


async def test_archive_itinerary(store_context: Any) -> None:
    """Generated itineraries should be archived in PG."""
    store, _, session_factory = store_context
    session_id = await store.create_session("u_1")
    itinerary = Itinerary(destination="杭州", total_cost=200)

    await store.archive_itinerary(session_id, "u_1", itinerary)

    async with session_factory() as session:
        records = (await session.scalars(select(ItineraryRecord))).all()

    assert len(records) == 1
    assert records[0].destination == "杭州"
    assert records[0].total_cost == 200


async def test_list_itineraries_filters_by_user_and_session(store_context: Any) -> None:
    """Archived itinerary listing should support user and session filters."""
    store, _, _ = store_context
    own_session = await store.create_session("u_1")
    other_session = await store.create_session("u_2")
    await store.archive_itinerary(own_session, "u_1", Itinerary(destination="杭州", total_cost=200))
    await store.archive_itinerary(
        other_session, "u_2", Itinerary(destination="北京", total_cost=300)
    )

    user_itineraries = await store.list_itineraries(user_id="u_1")
    session_itineraries = await store.list_itineraries(session_id=own_session)

    assert [item["destination"] for item in user_itineraries] == ["杭州"]
    assert [item["destination"] for item in session_itineraries] == ["杭州"]
    assert isinstance(user_itineraries[0]["content"], Itinerary)


async def test_list_sessions(store_context: Any) -> None:
    """Users should only see their own sessions."""
    store, _, _ = store_context
    own_session = await store.create_session("u_1", "自己的会话")
    await store.create_session("u_2", "别人的会话")

    sessions = await store.list_sessions("u_1")

    assert [session["session_id"] for session in sessions] == [own_session]
    assert sessions[0]["title"] == "自己的会话"


async def test_get_session_messages_returns_pg_history(store_context: Any) -> None:
    """Historical message view should read full PG history."""
    store, _, _ = store_context
    session_id = await store.create_session("u_1")
    await store.save_message(session_id, "user", "你好")

    messages = await store.get_session_messages(session_id)

    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"
    assert messages[0]["created_at"]


async def test_maybe_compress_triggers(store_context: Any) -> None:
    """Compression should update Redis hot history when threshold is exceeded."""
    _, redis, session_factory = store_context
    compressor = FakeCompressor(should_compress=True)
    store = SessionStore(redis, session_factory, compressor=compressor)
    session_id = await store.create_session("u_1")
    for index in range(4):
        await store.save_message(session_id, "user", f"消息{index}")

    compressed = await store.maybe_compress(session_id)
    messages = await store.load_messages(session_id)

    assert compressed is True
    assert messages[0]["content"].startswith("[对话摘要]")
    assert messages[-1]["content"] == "消息3"


async def test_maybe_compress_skips(store_context: Any) -> None:
    """Compression should be skipped below threshold."""
    _, redis, session_factory = store_context
    compressor = FakeCompressor(should_compress=False)
    store = SessionStore(redis, session_factory, compressor=compressor)
    session_id = await store.create_session("u_1")
    await store.save_message(session_id, "user", "短消息")

    compressed = await store.maybe_compress(session_id)

    assert compressed is False
