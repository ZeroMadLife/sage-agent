"""Long-term user preference memory backed by Mem0."""

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MemoryFact(BaseModel):
    """One long-term memory fact about a travel user."""

    content: str = Field(description="Memory content")
    score: float = Field(default=0.0, description="Relevance score")
    fact_id: str = Field(default="", description="Provider memory identifier")
    created_at: str = Field(default="", description="Creation timestamp when available")


class LongTermMemory:
    """Mem0-backed cross-session user preference memory."""

    def __init__(self, mem0_client: Any, user_id: str) -> None:
        self._mem0 = mem0_client
        self._user_id = user_id

    def search(self, query: str, limit: int = 10) -> list[MemoryFact]:
        """Search relevant memories for one user, degrading to no memory on failure."""
        try:
            raw_results = self._mem0.search(query=query, user_id=self._user_id, limit=limit)
        except Exception as exc:
            logger.warning("Mem0 search failed for user %s: %s", self._user_id, exc)
            return []

        results = self._unwrap_results(raw_results)
        facts: list[MemoryFact] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            content = item.get("memory", item.get("content", ""))
            facts.append(
                MemoryFact(
                    content=str(content),
                    score=self._to_float(item.get("score", 0.0)),
                    fact_id=str(item.get("id", item.get("fact_id", ""))),
                    created_at=str(item.get("created_at", "")),
                )
            )
        return facts

    async def extract_and_store(self, conversation: str) -> None:
        """Ask Mem0 to extract and store durable user preference facts."""
        try:
            self._mem0.add(conversation, user_id=self._user_id)
        except Exception as exc:
            logger.warning("Mem0 add failed for user %s: %s", self._user_id, exc)

    @staticmethod
    def format_facts_for_prompt(facts: list[MemoryFact]) -> str:
        """Format memory facts as compact planning prompt context."""
        items = [fact.content for fact in facts if fact.content]
        if not items:
            return ""
        return "已知用户偏好: " + "; ".join(items)

    @staticmethod
    def _unwrap_results(raw_results: Any) -> list[Any]:
        """Normalize Mem0 result envelopes across SDK versions."""
        if isinstance(raw_results, list):
            return raw_results
        if isinstance(raw_results, dict):
            results = raw_results.get("results")
            if isinstance(results, list):
                return results
            memories = raw_results.get("memories")
            if isinstance(memories, list):
                return memories
        return []

    @staticmethod
    def _to_float(value: Any) -> float:
        """Convert provider scores to floats."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
