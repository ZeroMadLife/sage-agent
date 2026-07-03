"""LangGraph StateGraph construction for Phase 2 agent orchestration."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.budget import create_budget_agent
from agents.info import create_info_agent
from agents.memory_node import create_memory_node
from agents.planning import create_planning_agent
from agents.recommend import create_recommend_agent
from agents.supervisor import should_replan
from core.state import TravelState

__all__ = ["build_graph", "should_replan"]


def build_graph(
    weather_client: Any,
    scenic_client: Any,
    planning_llm: Any,
    budget_llm: Any,
    memory_manager: Any | None = None,
) -> Any:
    """Build and compile the two-phase multi-agent graph."""
    info_agent = create_info_agent(weather_client, scenic_client)
    recommend_agent = create_recommend_agent(scenic_client)
    planning_agent = create_planning_agent(planning_llm)
    budget_agent = create_budget_agent(budget_llm)

    async def info_weather_node(state: dict[str, Any]) -> dict[str, Any]:
        """Run Info Agent but only publish weather_info in the parallel graph."""
        result = await info_agent(state)
        return {"weather_info": result.get("weather_info", {})}

    graph = StateGraph(TravelState)
    graph.add_node("info", info_weather_node)
    graph.add_node("recommend", recommend_agent)
    graph.add_node("planning", planning_agent)
    graph.add_node("budget", budget_agent)
    if memory_manager is not None:
        graph.add_node("memory", create_memory_node(memory_manager))

    graph.add_edge(START, "info")
    graph.add_edge(START, "recommend")
    if memory_manager is not None:
        graph.add_edge(["info", "recommend"], "memory")
        graph.add_edge("memory", "planning")
    else:
        graph.add_edge(["info", "recommend"], "planning")
    graph.add_edge("planning", "budget")
    graph.add_conditional_edges(
        "budget",
        should_replan,
        {
            "planning": "planning",
            END: END,
        },
    )

    return graph.compile()
