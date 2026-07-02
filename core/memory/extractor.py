"""Memory extraction and retrieval coordinator."""

import logging

from core.memory.long_term import LongTermMemory, MemoryFact

logger = logging.getLogger(__name__)


class MemoryManager:
    """Coordinate long-term memory extraction and planning-time retrieval."""

    def __init__(self, long_term: LongTermMemory) -> None:
        self._long_term = long_term

    async def extract_memories_async(
        self,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Extract durable memories from one interaction without surfacing failures."""
        if not user_message.strip() and not assistant_message.strip():
            return

        conversation = f"用户: {user_message}\n助手: {assistant_message}"
        try:
            await self._long_term.extract_and_store(conversation)
        except Exception as exc:
            logger.warning("Async memory extraction failed: %s", exc)

    def retrieve_for_planning(self, query: str, limit: int = 10) -> str:
        """Return prompt-ready memory context for planning."""
        try:
            facts = self._long_term.search(query, limit=limit)
            return self._long_term.format_facts_for_prompt(facts)
        except Exception as exc:
            logger.warning("Memory retrieval for planning failed: %s", exc)
            return ""

    def retrieve_facts(self, query: str, limit: int = 10) -> list[MemoryFact]:
        """Return raw memory facts for state, debugging, and future UX surfaces."""
        try:
            return self._long_term.search(query, limit=limit)
        except Exception as exc:
            logger.warning("Memory fact retrieval failed: %s", exc)
            return []
