"""Memory node tests."""

from unittest.mock import MagicMock

import pytest

from agents.memory_node import create_memory_node
from core.memory.extractor import MemoryFact


@pytest.fixture
def mock_memory_manager() -> MagicMock:
    """Create a memory manager test double."""
    manager = MagicMock()
    manager.retrieve_for_planning = MagicMock(
        return_value="已知用户偏好: 用户喜欢海鲜; 预算500元以内"
    )
    manager.retrieve_facts = MagicMock(
        return_value=[MemoryFact(content="用户喜欢海鲜", score=0.95, fact_id="1")]
    )
    return manager


def test_memory_node_returns_memory_context(mock_memory_manager: MagicMock) -> None:
    """memory_node returns prompt context and raw serializable facts."""
    node = create_memory_node(mock_memory_manager)
    state = {
        "destination": "杭州",
        "preferences": ["美食"],
        "messages": [],
    }

    result = node(state)

    assert "海鲜" in result["memory_context"]
    assert result["memory_facts"] == [{"content": "用户喜欢海鲜", "score": 0.95, "fact_id": "1"}]


def test_memory_node_queries_with_destination_and_preferences(
    mock_memory_manager: MagicMock,
) -> None:
    """Memory retrieval query includes destination and current preferences."""
    node = create_memory_node(mock_memory_manager)
    state = {
        "destination": "杭州",
        "preferences": ["美食", "自然风光"],
        "messages": [],
    }

    node(state)

    mock_memory_manager.retrieve_for_planning.assert_called_once()
    call_arg = mock_memory_manager.retrieve_for_planning.call_args.args[0]
    assert "杭州" in call_arg
    assert "美食" in call_arg
    assert "自然风光" in call_arg


def test_memory_node_handles_empty_state() -> None:
    """Empty state degrades to empty memory output."""
    manager = MagicMock()
    manager.retrieve_for_planning = MagicMock(return_value="")
    manager.retrieve_facts = MagicMock(return_value=[])
    node = create_memory_node(manager)

    result = node({"messages": []})

    assert result["memory_context"] == ""
    assert result["memory_facts"] == []


def test_memory_node_handles_manager_error() -> None:
    """Memory manager failures do not break graph execution."""
    manager = MagicMock()
    manager.retrieve_for_planning = MagicMock(side_effect=Exception("memory down"))
    manager.retrieve_facts = MagicMock(side_effect=Exception("memory down"))
    node = create_memory_node(manager)

    result = node({"destination": "杭州", "messages": []})

    assert result["memory_context"] == ""
    assert result["memory_facts"] == []
