"""LangGraph memory retrieval node."""

import logging
from collections.abc import Callable
from typing import Any

from core.memory.extractor import MemoryManager
from core.state import TravelState

logger = logging.getLogger(__name__)


def create_memory_node(
    memory_manager: MemoryManager,
) -> Callable[[TravelState], dict[str, Any]]:
    """Create a node that retrieves user memories before planning."""

    def _node(state: TravelState) -> dict[str, Any]:
        destination = str(state.get("destination", ""))
        preferences = state.get("preferences", [])
        prefs_text = (
            " ".join(str(preference) for preference in preferences)
            if isinstance(preferences, list)
            else ""
        )
        query = f"{destination} {prefs_text} 行程偏好".strip()

        try:
            memory_context = memory_manager.retrieve_for_planning(query)
        except Exception as exc:
            logger.warning("Memory context retrieval failed: %s", exc)
            memory_context = ""

        try:
            memory_facts = memory_manager.retrieve_facts(query)
        except Exception as exc:
            logger.warning("Memory fact retrieval failed: %s", exc)
            memory_facts = []

        return {
            "memory_context": memory_context,
            "memory_facts": [
                {
                    "content": fact.content,
                    "score": fact.score,
                    "fact_id": fact.fact_id,
                }
                for fact in memory_facts
            ],
        }

    return _node
