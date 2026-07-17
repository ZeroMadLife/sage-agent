"""Supervisor routing helpers for multi-agent orchestration."""

import logging

from langgraph.graph import END

from core.state import TravelState

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3


def should_replan(state: TravelState) -> str:
    """Route budget results back to planning or end the graph."""
    over_budget = bool(state.get("over_budget", False))
    iteration_count = int(state.get("iteration_count", 0))

    if over_budget and iteration_count < MAX_ITERATIONS:
        logger.info(
            "Budget exceeded; routing back to planning (%d/%d)",
            iteration_count,
            MAX_ITERATIONS,
        )
        return "planning"

    if over_budget:
        logger.warning("Budget replan limit reached; accepting current itinerary")

    return END
