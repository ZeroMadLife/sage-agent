"""Memory demo script tests."""

from core.memory.long_term import MemoryFact
from scripts.demo_memory import memory_contains_preference, render_memory_facts


def test_render_memory_facts_formats_facts() -> None:
    """Memory facts render into a compact human-readable line."""
    facts = [
        MemoryFact(content="用户喜欢海鲜", score=0.95, fact_id="1"),
        MemoryFact(content="预算500元以内", score=0.88, fact_id="2"),
    ]

    text = render_memory_facts(facts)

    assert "用户喜欢海鲜" in text
    assert "预算500元以内" in text


def test_render_memory_facts_handles_empty_list() -> None:
    """Empty facts render as an explicit empty marker."""
    assert render_memory_facts([]) == "未检索到记忆"


def test_memory_contains_preference_detects_context_or_content() -> None:
    """Preference detection checks both memory context and LLM output."""
    assert memory_contains_preference("已知用户偏好: 用户喜欢海鲜", "推荐河鲜餐厅") is True
    assert memory_contains_preference("", "晚餐安排海鲜小馆") is True
    assert memory_contains_preference("已知用户偏好: 喜欢博物馆", "推荐西湖") is False
