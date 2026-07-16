"""LangGraph StateGraph construction for Phase 2 agent orchestration."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.budget import create_budget_agent
from agents.info import create_info_agent
from agents.memory_node import create_memory_node
from agents.planning import create_planning_agent
from agents.recommend import create_recommend_agent
from agents.supervisor import should_replan
from core.memory.extractor import MemoryManager
from core.state import TravelState

__all__ = ["build_graph", "should_replan"]


def build_graph(
    weather_client: Any,
    scenic_client: Any,
    planning_llm: Any,
    budget_llm: Any,
    memory_manager: MemoryManager | None = None,
) -> Any:
    """Build and compile the two-phase multi-agent graph."""
    info_agent = create_info_agent(weather_client, scenic_client)
    recommend_agent = create_recommend_agent(scenic_client)
    planning_agent = create_planning_agent(planning_llm)
    budget_agent = create_budget_agent(budget_llm)

    async def info_weather_node(state: TravelState) -> dict[str, Any]:
        """Run Info Agent but only publish weather_info in the parallel graph."""
        result = await info_agent(state)
        return {"weather_info": result.get("weather_info", {})}

    def recommend_graph_node(state: TravelState) -> dict[str, Any]:
        """Expose the configured recommendation callable as a typed graph node."""
        return recommend_agent(state)

    async def planning_graph_node(state: TravelState) -> dict[str, Any]:
        """Expose the configured planning callable as a typed graph node."""
        return await planning_agent(state)

    async def budget_graph_node(state: TravelState) -> dict[str, Any]:
        """Expose the configured budget callable as a typed graph node."""
        return await budget_agent(state)

    graph: StateGraph[TravelState, None, TravelState, TravelState] = StateGraph(
        state_schema=TravelState,
        input_schema=TravelState,
        output_schema=TravelState,
    )
    graph.add_node("info", info_weather_node, input_schema=TravelState)
    graph.add_node("recommend", recommend_graph_node, input_schema=TravelState)
    graph.add_node("planning", planning_graph_node, input_schema=TravelState)
    graph.add_node("budget", budget_graph_node, input_schema=TravelState)
    if memory_manager is not None:
        memory_agent = create_memory_node(memory_manager)

        def memory_graph_node(state: TravelState) -> dict[str, Any]:
            """Expose the configured memory callable as a typed graph node."""
            return memory_agent(state)

        graph.add_node(
            "memory",
            memory_graph_node,
            input_schema=TravelState,
        )

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
