"""TourSwarm CLI demo for multi-agent itinerary planning.

Usage:
    python -m scripts.demo_chat "周末去杭州2日游预算500元喜欢美食"
    python -m scripts.demo_chat
"""

import asyncio
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agents.graph import build_graph
from core.config.settings import get_settings
from core.llm import create_llm
from mcp_servers.scenic.client import ScenicClient
from mcp_servers.weather.client import WeatherClient
from models.itinerary import Itinerary

DEFAULT_DATES = {"start": "2026-07-05", "end": "2026-07-06"}
DEFAULT_PLANNING_MODEL = "doubao:Doubao-Seed-2.0-pro"


def parse_input(text: str) -> dict[str, Any]:
    """Parse a simple Chinese travel request into initial graph state."""
    destination = "杭州"
    budget = 500

    budget_match = re.search(r"(?:预算)?\s*(\d+)\s*元", text)
    if budget_match is not None:
        budget = int(budget_match.group(1))

    for city in ["杭州", "北京", "上海", "南京", "苏州", "成都", "西安", "厦门"]:
        if city in text:
            destination = city
            break

    preferences: list[str] = []
    pref_keywords = {
        "美食": ["美食", "吃", "小吃"],
        "自然风光": ["自然", "山水", "湖", "风景"],
        "人文古迹": ["历史", "古迹", "寺庙", "博物馆"],
        "购物": ["购物", "逛街", "买"],
    }
    for preference, keywords in pref_keywords.items():
        if any(keyword in text for keyword in keywords):
            preferences.append(preference)

    return {
        "destination": destination,
        "budget_total": budget,
        "preferences": preferences,
        "dates": dict(DEFAULT_DATES),
    }


def _planning_model_spec(configured_model: str) -> str:
    """Resolve the planning model spec with a Phase 2 safe default."""
    return configured_model if ":" in configured_model else DEFAULT_PLANNING_MODEL


def _print_itinerary(itinerary: Itinerary) -> None:
    """Print a concise itinerary summary."""
    print(f"\n{itinerary.destination} itinerary: {len(itinerary.days)} day(s)")
    print(f"Weather: {itinerary.weather_summary}")
    print(f"Total cost: {itinerary.total_cost} CNY")

    for index, day in enumerate(itinerary.days, start=1):
        print(f"\nDay {index} {day.date}")
        for spot in day.spots:
            print(
                f"  {spot.arrival_time}-{spot.departure_time} {spot.name} "
                f"(ticket {spot.ticket_price} CNY, {spot.duration_hours}h)"
            )
        for meal in day.meals:
            print(f"  Meal: {meal.name} ({meal.estimated_cost} CNY)")
        print(f"  Day cost: {day.total_cost} CNY")


async def run_demo(user_input: str) -> None:
    """Run the CLI demo with real configured clients and LLMs."""
    load_dotenv()
    settings = get_settings()
    repo_root = Path(__file__).resolve().parent.parent

    weather_client = WeatherClient(
        api_key=settings.qweather_api_key,
        base_url=settings.qweather_base_url,
        geo_url=settings.qweather_geo_url,
    )
    scenic_client = ScenicClient(data_path=str(repo_root / "data" / "mock" / "scenic_spots.json"))
    model_spec = _planning_model_spec(settings.llm_model)
    planning_llm = create_llm(model_spec)
    budget_llm = create_llm(model_spec)
    graph = build_graph(
        weather_client=weather_client,
        scenic_client=scenic_client,
        planning_llm=planning_llm,
        budget_llm=budget_llm,
    )

    parsed = parse_input(user_input)
    initial_state = {
        "messages": [],
        "iteration_count": 0,
        **parsed,
    }

    print("=" * 60)
    print("TourSwarm CLI demo")
    print("=" * 60)
    print(f"Destination: {parsed['destination']}")
    print(f"Budget: {parsed['budget_total']} CNY")
    print(f"Preferences: {', '.join(parsed['preferences']) or 'none'}")
    print(f"Dates: {parsed['dates']['start']} ~ {parsed['dates']['end']}")
    print("-" * 60)
    print("Running agents...")

    try:
        result = await graph.ainvoke(initial_state)
    finally:
        await weather_client.close()

    itinerary = result.get("itinerary")
    if isinstance(itinerary, Itinerary):
        _print_itinerary(itinerary)
    else:
        print("No itinerary was generated.")

    budget = result.get("budget_breakdown")
    if budget is not None:
        status = "over budget" if budget.over_budget else "within budget"
        print(f"\nBudget: {budget.spent}/{budget.total} CNY ({status})")


def main() -> None:
    """CLI entrypoint."""
    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
    else:
        user_input = input("请输入旅游需求（输入 q 退出）: ")
        if user_input.strip().lower() == "q":
            return

    asyncio.run(run_demo(user_input))


if __name__ == "__main__":
    main()
