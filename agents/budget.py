"""Budget Agent for budget allocation and over-budget detection."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from models.itinerary import BudgetBreakdown, Itinerary

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_RATIOS: dict[str, float] = {
    "transport": 0.30,
    "accommodation": 0.25,
    "food": 0.25,
    "tickets": 0.15,
    "misc": 0.05,
}


def calculate_budget_breakdown(
    itinerary: Itinerary,
    budget_total: int,
    transport: int = 0,
    accommodation: int = 0,
    food: int = 0,
    tickets: int = 0,
    misc: int = 0,
) -> BudgetBreakdown:
    """Calculate spend summary and over-budget status."""
    spent = itinerary.total_cost
    return BudgetBreakdown(
        total=budget_total,
        spent=spent,
        transport=transport,
        accommodation=accommodation,
        food=food,
        tickets=tickets,
        misc=misc,
        over_budget=spent > budget_total,
    )


async def budget_node(state: dict[str, Any], llm: Any) -> dict[str, Any]:
    """Check itinerary spend against the user's total budget."""
    _ = llm
    itinerary = state["itinerary"]
    if not isinstance(itinerary, Itinerary):
        itinerary = Itinerary.model_validate(itinerary)

    budget_total = int(state.get("budget_total", 0))
    iteration_count = int(state.get("iteration_count", 0))
    breakdown = calculate_budget_breakdown(
        itinerary=itinerary,
        budget_total=budget_total,
        transport=int(budget_total * DEFAULT_BUDGET_RATIOS["transport"]),
        accommodation=int(budget_total * DEFAULT_BUDGET_RATIOS["accommodation"]),
        food=int(budget_total * DEFAULT_BUDGET_RATIOS["food"]),
        tickets=int(budget_total * DEFAULT_BUDGET_RATIOS["tickets"]),
        misc=int(budget_total * DEFAULT_BUDGET_RATIOS["misc"]),
    )

    if breakdown.over_budget:
        logger.warning(
            "Budget exceeded: spent=%d budget=%d iteration=%d",
            breakdown.spent,
            budget_total,
            iteration_count + 1,
        )

    return {
        "budget_breakdown": breakdown,
        "over_budget": breakdown.over_budget,
        "iteration_count": iteration_count + 1,
    }


def create_budget_agent(llm: Any) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Create a LangGraph-compatible Budget Agent node."""

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        return await budget_node(state, llm)

    return _node
