"""Run the LangGraph itinerary planner and emit UI-friendly events."""

import time
from collections.abc import AsyncIterator
from typing import Any

from api.schemas import ErrorEvent, ProgressEvent, ResultEvent
from core.verifier import verify_itinerary
from models.itinerary import Itinerary
from scripts.demo_chat import parse_input


async def run_chat(
    graph: Any,
    user_id: str,
    session_id: str,
    content: str,
) -> AsyncIterator[ProgressEvent | ResultEvent | ErrorEvent]:
    """Execute one chat request and stream coarse-grained events."""
    started = time.perf_counter()
    parsed = parse_input(content)
    initial_state = {
        "messages": [],
        "user_id": user_id,
        "session_id": session_id,
        "iteration_count": 0,
        **parsed,
    }

    yield ProgressEvent(agent="supervisor", message="已理解需求，开始调度信息与推荐Agent")
    yield ProgressEvent(agent="info", message="正在查询天气与目的地信息")
    yield ProgressEvent(agent="recommend", message="正在筛选候选景点")

    try:
        result = await graph.ainvoke(initial_state)
    except Exception as exc:
        yield ErrorEvent(message=f"Agent execution failed: {exc}", recoverable=True)
        return

    itinerary = result.get("itinerary")
    if not isinstance(itinerary, Itinerary):
        yield ErrorEvent(message="Agent did not produce a valid itinerary", recoverable=True)
        return

    validation = verify_itinerary(
        itinerary=itinerary,
        dates=parsed["dates"],
        budget_total=int(parsed["budget_total"]),
        weather_info=result.get("weather_info", {}),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    yield ResultEvent(
        itinerary=itinerary,
        validation=validation.model_dump(),
        metrics={"latency_ms": latency_ms},
    )
