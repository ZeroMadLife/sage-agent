"""Memory extraction and retrieval coordinator."""

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)
TRAVEL_PLANNING_MEMORY_SCOPE = "skill:travel-planning"


@dataclass(frozen=True, slots=True)
class MemoryFact:
    """Provider-neutral long-term memory fact."""

    content: str
    score: float = 0.0
    fact_id: str = ""
    created_at: str = ""


class LongTermMemoryPort(Protocol):
    """Minimal memory capability accepted by the legacy travel graph."""

    async def extract_and_store(self, conversation: str, scope: str | None = None) -> None: ...

    def search(
        self,
        query: str,
        limit: int = 10,
        scope: str | None = None,
    ) -> list[MemoryFact]: ...

    def format_facts_for_prompt(self, facts: list[MemoryFact]) -> str: ...


class MemoryManager:
    """Coordinate long-term memory extraction and planning-time retrieval."""

    def __init__(
        self,
        long_term: LongTermMemoryPort,
        default_scope: str = TRAVEL_PLANNING_MEMORY_SCOPE,
    ) -> None:
        self._long_term = long_term
        self._default_scope = default_scope

    async def extract_memories_async(
        self,
        user_message: str,
        assistant_message: str,
        scope: str | None = None,
    ) -> None:
        """Extract durable memories from one interaction without surfacing failures."""
        if not user_message.strip() and not assistant_message.strip():
            return

        conversation = f"用户: {user_message}\n助手: {assistant_message}"
        try:
            await self._long_term.extract_and_store(
                conversation,
                scope=scope or self._default_scope,
            )
        except Exception as exc:
            logger.warning("Async memory extraction failed: %s", exc)

    def retrieve_for_planning(
        self,
        query: str,
        limit: int = 10,
        scope: str | None = None,
    ) -> str:
        """Return prompt-ready memory context for planning."""
        try:
            facts = self._long_term.search(query, limit=limit, scope=scope or self._default_scope)
            return self._long_term.format_facts_for_prompt(facts)
        except Exception as exc:
            logger.warning("Memory retrieval for planning failed: %s", exc)
            return ""

    def retrieve_facts(
        self,
        query: str,
        limit: int = 10,
        scope: str | None = None,
    ) -> list[MemoryFact]:
        """Return raw memory facts for state, debugging, and future UX surfaces."""
        try:
            return self._long_term.search(
                query,
                limit=limit,
                scope=scope or self._default_scope,
            )
        except Exception as exc:
            logger.warning("Memory fact retrieval failed: %s", exc)
            return []
