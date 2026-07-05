"""Long-term memory scope isolation tests."""

from typing import Any
from unittest.mock import MagicMock

from core.memory.long_term import LongTermMemory


class ScopedMem0Fake:
    """Tiny Mem0 fake that honors the scope filter contract."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(
        self,
        messages: str,
        user_id: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, str]:
        metadata = metadata or {}
        self.items.append(
            {
                "memory": messages,
                "score": 1.0,
                "id": f"mem_{len(self.items)}",
                "metadata": metadata,
                "user_id": user_id,
            }
        )
        return {"id": "ok"}

    def search(
        self,
        query: str,
        user_id: str,
        limit: int,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        _ = query
        scope = (filters or {}).get("scope")
        return [
            item
            for item in self.items
            if item["user_id"] == user_id and item["metadata"].get("scope") == scope
        ][:limit]


async def test_extract_and_store_writes_scope_metadata() -> None:
    """LongTermMemory writes the selected scope into Mem0 metadata."""
    mem0 = MagicMock()
    memory = LongTermMemory(mem0_client=mem0, user_id="user_123")

    await memory.extract_and_store("用户: 我喜欢海鲜", scope="skill:travel-planning")

    mem0.add.assert_called_once_with(
        "用户: 我喜欢海鲜",
        user_id="user_123",
        metadata={"scope": "skill:travel-planning"},
    )


def test_search_filters_by_scope() -> None:
    """LongTermMemory passes explicit scope filters to Mem0 search."""
    mem0 = MagicMock(return_value=[])
    memory = LongTermMemory(mem0_client=mem0, user_id="user_123")

    memory.search("偏好", scope="session:abc", limit=3)

    mem0.search.assert_called_once_with(
        query="偏好",
        user_id="user_123",
        limit=3,
        filters={"scope": "session:abc"},
    )


async def test_skill_scope_memories_do_not_cross_between_skills() -> None:
    """Skill-level memories are visible only when searching the same skill scope."""
    mem0 = ScopedMem0Fake()
    memory = LongTermMemory(mem0_client=mem0, user_id="user_123")

    await memory.extract_and_store("用户: 我喜欢海鲜", scope="skill:travel-planning")
    await memory.extract_and_store("用户: 我用 Mac 写代码", scope="skill:code-assistant")

    travel_facts = memory.search("用户偏好", scope="skill:travel-planning")
    code_facts = memory.search("用户偏好", scope="skill:code-assistant")

    assert [fact.content for fact in travel_facts] == ["用户: 我喜欢海鲜"]
    assert [fact.content for fact in code_facts] == ["用户: 我用 Mac 写代码"]


async def test_default_scope_is_user_scope_for_backward_compatible_calls() -> None:
    """Old callers can omit scope and still get a deterministic user-level scope."""
    mem0 = ScopedMem0Fake()
    memory = LongTermMemory(mem0_client=mem0, user_id="user_123")

    await memory.extract_and_store("用户: 我是学生")

    facts = memory.search("用户身份")

    assert [fact.content for fact in facts] == ["用户: 我是学生"]
