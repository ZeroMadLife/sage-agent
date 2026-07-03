"""Long-term Mem0-backed preference memory tests."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from core.memory.long_term import LongTermMemory, MemoryFact


@pytest.fixture
def mock_mem0() -> MagicMock:
    """Create a Mem0 client test double."""
    client = MagicMock()
    client.search = MagicMock(
        return_value=[
            {"memory": "用户喜欢海鲜", "score": 0.95, "id": "mem_001"},
            {"memory": "用户偏好自然风光", "score": 0.88, "id": "mem_002"},
        ]
    )
    client.add = MagicMock(return_value={"id": "mem_003"})
    return client


@pytest.fixture
def memory(mock_mem0: MagicMock) -> LongTermMemory:
    """Create long-term memory for one test user."""
    return LongTermMemory(mem0_client=mock_mem0, user_id="user_123")


def test_memory_fact_is_pydantic_model() -> None:
    """Memory facts are serializable Pydantic models."""
    fact = MemoryFact(content="用户喜欢海鲜", score=0.95, fact_id="mem_001")

    assert fact.model_dump()["content"] == "用户喜欢海鲜"


def test_search_returns_memory_facts(
    memory: LongTermMemory,
    mock_mem0: MagicMock,
) -> None:
    """Search returns normalized MemoryFact objects."""
    facts = memory.search("用户喜欢什么食物")

    assert len(facts) == 2
    assert facts[0].content == "用户喜欢海鲜"
    assert facts[0].score == 0.95
    assert facts[0].fact_id == "mem_001"
    mock_mem0.search.assert_called_once_with(
        query="用户喜欢什么食物",
        user_id="user_123",
        limit=10,
    )


def test_search_accepts_mem0_results_wrapped_in_results_key(mock_mem0: MagicMock) -> None:
    """Some Mem0 clients return a dict with a results list."""
    mock_mem0.search.return_value = {
        "results": [{"memory": "用户预算通常500元以内", "score": "0.77", "id": 123}]
    }
    memory = LongTermMemory(mem0_client=mock_mem0, user_id="user_123")

    facts = memory.search("预算偏好")

    assert facts == [MemoryFact(content="用户预算通常500元以内", score=0.77, fact_id="123")]


def test_search_returns_empty_on_error(memory: LongTermMemory) -> None:
    """Mem0 search failures degrade to no memory."""
    memory._mem0.search = MagicMock(side_effect=Exception("connection error"))

    facts = memory.search("什么食物")

    assert facts == []


async def test_extract_and_store_calls_mem0_add(
    memory: LongTermMemory,
    mock_mem0: MagicMock,
) -> None:
    """Conversation extraction delegates to Mem0 add with user scope."""
    await memory.extract_and_store(conversation="用户: 我对海鲜过敏\n助手: 好的，我会避免推荐海鲜")

    mock_mem0.add.assert_called_once_with(
        "用户: 我对海鲜过敏\n助手: 好的，我会避免推荐海鲜",
        user_id="user_123",
    )


async def test_extract_and_store_offloads_mem0_add(
    memory: LongTermMemory,
    mock_mem0: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mem0 add runs through asyncio.to_thread to avoid blocking the event loop."""
    calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    async def fake_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr("core.memory.long_term.asyncio.to_thread", fake_to_thread)

    await memory.extract_and_store(conversation="用户: 我喜欢爬山")

    assert calls == [
        (
            mock_mem0.add,
            ("用户: 我喜欢爬山",),
            {"user_id": "user_123"},
        )
    ]


async def test_extract_and_store_handles_error(
    memory: LongTermMemory,
    mock_mem0: MagicMock,
) -> None:
    """Mem0 storage failures do not break the caller."""
    mock_mem0.add = MagicMock(side_effect=Exception("storage error"))

    await memory.extract_and_store(conversation="用户: 我喜欢爬山")


def test_format_facts_for_prompt(memory: LongTermMemory) -> None:
    """Facts are formatted into compact prompt context."""
    facts = [
        MemoryFact(content="用户喜欢海鲜", score=0.95, fact_id="1"),
        MemoryFact(content="预算敏感，通常500元以内", score=0.88, fact_id="2"),
    ]

    text = memory.format_facts_for_prompt(facts)

    assert text == "已知用户偏好: 用户喜欢海鲜; 预算敏感，通常500元以内"


def test_format_facts_empty_returns_empty_string(memory: LongTermMemory) -> None:
    """No facts produces no prompt injection."""
    assert memory.format_facts_for_prompt([]) == ""


def test_format_facts_skips_blank_content(memory: LongTermMemory) -> None:
    """Blank facts do not add empty prompt bullets."""
    facts = [
        MemoryFact(content="", score=0.9, fact_id="1"),
        MemoryFact(content="用户偏好博物馆", score=0.8, fact_id="2"),
    ]

    assert memory.format_facts_for_prompt(facts) == "已知用户偏好: 用户偏好博物馆"
