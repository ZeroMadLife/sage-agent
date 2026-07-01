"""Shared LangGraph state for TourSwarm agent orchestration."""

from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages

from models.itinerary import BudgetBreakdown, Itinerary


class TravelState(TypedDict, total=False):
    """Global state shared by all travel agents.

    Each node returns partial updates for the fields it owns. LangGraph merges
    messages with the add_messages reducer while regular fields are replaced by
    the newest node output.
    """

    messages: Annotated[list[Any], add_messages]
    user_id: str
    session_id: str

    intent: str

    destination: str
    budget_total: int
    dates: dict[str, str]
    preferences: list[str]

    itinerary: Itinerary
    recommendations: list[dict[str, Any]]
    weather_info: dict[str, Any]
    budget_breakdown: BudgetBreakdown

    iteration_count: int
    over_budget: bool
    final_response: str
