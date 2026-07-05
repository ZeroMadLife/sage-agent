"""Memory extraction and prompt injection manager tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.extractor import MemoryManager
from core.memory.long_term import LongTermMemory, MemoryFact


@pytest.fixture
def mock_long_term() -> MagicMock:
    """Create a long-term memory test double."""
    long_term = MagicMock(spec=LongTermMemory)
    long_term.search = MagicMock(
        return_value=[
            MemoryFact(content="用户喜欢海鲜", score=0.95, fact_id="1"),
            MemoryFact(content="预算500元以内", score=0.88, fact_id="2"),
        ]
    )
    long_term.extract_and_store = AsyncMock()
    long_term.format_facts_for_prompt = MagicMock(
        return_value="已知用户偏好: 用户喜欢海鲜; 预算500元以内"
    )
    return long_term


@pytest.fixture
def manager(mock_long_term: MagicMock) -> MemoryManager:
    """Create a MemoryManager with mocked dependencies."""
    return MemoryManager(long_term=mock_long_term)


async def test_extract_memories_async_does_not_block(
    manager: MemoryManager,
    mock_long_term: MagicMock,
) -> None:
    """Async extraction delegates to long-term memory."""
    await manager.extract_memories_async(
        user_message="我喜欢海鲜",
        assistant_message="好的，会推荐海鲜餐厅",
    )

    mock_long_term.extract_and_store.assert_awaited_once_with(
        "用户: 我喜欢海鲜\n助手: 好的，会推荐海鲜餐厅",
        scope="skill:travel-planning",
    )


async def test_extract_memories_async_handles_error(
    manager: MemoryManager,
    mock_long_term: MagicMock,
) -> None:
    """Extraction failures do not bubble into the main graph."""
    mock_long_term.extract_and_store = AsyncMock(side_effect=Exception("error"))

    await manager.extract_memories_async("msg", "reply")


async def test_extract_memories_async_ignores_empty_messages(
    manager: MemoryManager,
    mock_long_term: MagicMock,
) -> None:
    """Empty interactions are not sent to Mem0."""
    await manager.extract_memories_async("", "")

    mock_long_term.extract_and_store.assert_not_awaited()


def test_retrieve_for_planning_returns_prompt_text(
    manager: MemoryManager,
    mock_long_term: MagicMock,
) -> None:
    """Planning retrieval returns prompt-ready preference text."""
    text = manager.retrieve_for_planning("杭州美食行程")

    assert "海鲜" in text
    assert "500" in text
    mock_long_term.search.assert_called_once_with(
        "杭州美食行程",
        limit=10,
        scope="skill:travel-planning",
    )


def test_retrieve_for_planning_returns_empty_on_no_memory() -> None:
    """No memory produces no prompt injection."""
    long_term = MagicMock(spec=LongTermMemory)
    long_term.search = MagicMock(return_value=[])
    long_term.format_facts_for_prompt = MagicMock(return_value="")
    manager = MemoryManager(long_term=long_term)

    text = manager.retrieve_for_planning("什么行程")

    assert text == ""


def test_retrieve_for_planning_handles_error(mock_long_term: MagicMock) -> None:
    """Memory lookup failures degrade to an empty prompt section."""
    mock_long_term.search = MagicMock(side_effect=Exception("search down"))
    manager = MemoryManager(long_term=mock_long_term)

    assert manager.retrieve_for_planning("杭州") == ""


def test_retrieve_facts_returns_list(
    manager: MemoryManager,
    mock_long_term: MagicMock,
) -> None:
    """Raw facts can be retrieved for state/debug UI."""
    facts = manager.retrieve_facts("用户偏好")

    assert len(facts) == 2
    assert facts[0].content == "用户喜欢海鲜"
    mock_long_term.search.assert_called_once_with(
        "用户偏好",
        limit=10,
        scope="skill:travel-planning",
    )


def test_retrieve_facts_handles_error(mock_long_term: MagicMock) -> None:
    """Raw fact retrieval also degrades gracefully."""
    mock_long_term.search = MagicMock(side_effect=Exception("search down"))
    manager = MemoryManager(long_term=mock_long_term)

    assert manager.retrieve_facts("用户偏好") == []
