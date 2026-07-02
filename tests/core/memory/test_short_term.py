"""Short-term Redis-backed session memory tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.short_term import SESSION_TTL_SECONDS, ShortTermMemory


@pytest.fixture
def mock_redis() -> MagicMock:
    """Create a Redis client test double."""
    return MagicMock()


@pytest.fixture
def memory(mock_redis: MagicMock) -> ShortTermMemory:
    """Create short-term memory for one test session."""
    return ShortTermMemory(redis_client=mock_redis, session_id="test-session", max_turns=20)


def test_short_term_memory_creates_key_with_session_id(memory: ShortTermMemory) -> None:
    """Redis key includes the session id for isolation."""
    assert "test-session" in memory.key


async def test_save_message_stores_in_redis(
    memory: ShortTermMemory,
    mock_redis: MagicMock,
) -> None:
    """Saving a message appends it and refreshes the 30 minute TTL."""
    mock_redis.get.return_value = None

    await memory.save_message(role="user", content="我喜欢海鲜")

    mock_redis.setex.assert_called_once()
    key, ttl, payload = mock_redis.setex.call_args.args
    assert "test-session" in key
    assert ttl == SESSION_TTL_SECONDS
    assert json.loads(payload) == [{"role": "user", "content": "我喜欢海鲜"}]


async def test_load_messages_returns_empty_for_new_session(
    memory: ShortTermMemory,
    mock_redis: MagicMock,
) -> None:
    """A new session has no messages."""
    mock_redis.get.return_value = None

    messages = await memory.load_messages()

    assert messages == []


async def test_load_messages_decodes_bytes_payload(
    memory: ShortTermMemory,
    mock_redis: MagicMock,
) -> None:
    """Stored Redis bytes payloads are decoded as JSON."""
    stored = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    mock_redis.get.return_value = json.dumps(stored, ensure_ascii=False).encode()

    messages = await memory.load_messages()

    assert messages == stored


async def test_load_messages_ignores_invalid_payload(
    memory: ShortTermMemory,
    mock_redis: MagicMock,
) -> None:
    """Corrupted Redis payloads degrade to an empty message list."""
    mock_redis.get.return_value = "{not-json"

    messages = await memory.load_messages()

    assert messages == []


async def test_clear_session_deletes_key(
    memory: ShortTermMemory,
    mock_redis: MagicMock,
) -> None:
    """Clearing a session deletes the Redis key."""
    await memory.clear()

    mock_redis.delete.assert_called_once_with(memory.key)


def test_should_summarize_returns_false_under_threshold(memory: ShortTermMemory) -> None:
    """Message count under max_turns * 2 does not need compression."""
    messages = [{"role": "user", "content": "msg"}] * 10

    assert memory.should_summarize(messages) is False


def test_should_summarize_returns_true_over_threshold(memory: ShortTermMemory) -> None:
    """More than max_turns turns should trigger summary compression."""
    messages = [{"role": "user", "content": "msg"}] * 50

    assert memory.should_summarize(messages) is True


async def test_summarize_uses_llm_and_keeps_recent_messages(memory: ShortTermMemory) -> None:
    """Summary compression replaces older messages while keeping recent turns."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="用户喜欢海鲜和自然风光"))
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "系统提示"},
        *[
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"旧消息{index}"}
            for index in range(10)
        ],
    ]

    result = await memory.summarize(messages, llm=mock_llm)

    assert len(result) < len(messages)
    assert result[0] == {"role": "system", "content": "系统提示"}
    assert any("海鲜" in message.get("content", "") for message in result)
    assert result[-1]["content"] == "旧消息9"


async def test_summarize_returns_original_messages_on_llm_error(
    memory: ShortTermMemory,
) -> None:
    """LLM summary failures keep the original history instead of dropping context."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("llm unavailable"))
    messages: list[dict[str, str]] = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"消息{index}"}
        for index in range(10)
    ]

    result = await memory.summarize(messages, llm=mock_llm)

    assert result == messages


async def test_async_redis_methods_are_supported() -> None:
    """Async Redis clients can also be used by the same memory wrapper."""
    redis_client = MagicMock()
    redis_client.get = AsyncMock(return_value=None)
    redis_client.setex = AsyncMock()
    memory = ShortTermMemory(redis_client=redis_client, session_id="async-session")

    await memory.save_message("user", "你好")

    redis_client.setex.assert_awaited_once()
