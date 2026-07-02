"""TourSwarm cross-session memory demo.

Usage:
    python -m scripts.demo_memory
"""

import asyncio
from typing import Any

from dotenv import load_dotenv

from agents.planning import PLANNING_SYSTEM_PROMPT, create_planning_prompt
from core.config.settings import get_settings
from core.llm import create_llm
from core.memory.extractor import MemoryManager
from core.memory.long_term import LongTermMemory, MemoryFact
from core.memory.mem0_factory import create_mem0_client


def render_memory_facts(facts: list[MemoryFact]) -> str:
    """Render retrieved facts for CLI display."""
    if not facts:
        return "未检索到记忆"
    return "；".join(fact.content for fact in facts if fact.content) or "未检索到记忆"


def memory_contains_preference(memory_context: str, generated_content: str) -> bool:
    """Return whether seafood preference is visible in memory or generated output."""
    combined = f"{memory_context}\n{generated_content}"
    return "海鲜" in combined


async def run_memory_demo() -> None:
    """Run a two-session memory demonstration with real configured services."""
    load_dotenv()
    settings = get_settings()

    print("=" * 60)
    print("TourSwarm memory demo")
    print("=" * 60)

    print("\n[Init] Connecting Mem0 + Qdrant...")
    mem0_client = create_mem0_client()
    if mem0_client is None:
        print("Mem0 unavailable. Start Qdrant/Docker and verify Mem0 config before rerunning.")
        return

    user_id = "demo_user_memory_001"
    long_term = LongTermMemory(mem0_client=mem0_client, user_id=user_id)
    memory_manager = MemoryManager(long_term=long_term)

    print("\n[Session 1] User states a durable preference")
    user_message = "我下周想去杭州玩，我个人特别喜欢海鲜，预算大概500块"
    assistant_message = "好的，我会在杭州行程里优先考虑海鲜和500元左右的预算。"
    print(f"User: {user_message}")
    print(f"Assistant: {assistant_message}")
    await memory_manager.extract_memories_async(user_message, assistant_message)
    print("Memory extraction requested.")

    print("\n[Session 2] New session retrieves preference")
    query = "杭州 周末 美食 行程偏好"
    facts = memory_manager.retrieve_facts(query)
    memory_context = memory_manager.retrieve_for_planning(query)
    print(f"Retrieved facts: {render_memory_facts(facts)}")
    print(f"Prompt context: {memory_context or '无'}")

    planning_llm = create_llm(settings.llm_model)
    prompt = create_planning_prompt(
        destination="杭州",
        dates={"start": "2026-07-12", "end": "2026-07-13"},
        budget_total=500,
        preferences=["美食"],
        weather_info={"error": True},
        recommendations=[
            {"id": "hangzhou-xihu", "name": "西湖", "ticket_price": 0, "rating": 4.8},
            {"id": "hangzhou-hefangjie", "name": "河坊街", "ticket_price": 0, "rating": 4.3},
        ],
        memory_context=memory_context,
    )

    print("\n[Planning] Generating itinerary with memory context...")
    response = await planning_llm.ainvoke(
        [
            {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
    )
    content = _content_text(response)
    print(content[:800])

    has_preference = memory_contains_preference(memory_context, content)
    print(f"\nMemory influence: {'visible' if has_preference else 'not visible'}")


def _content_text(response: Any) -> str:
    """Extract text content from a LangChain response."""
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


def main() -> None:
    """CLI entrypoint."""
    asyncio.run(run_memory_demo())


if __name__ == "__main__":
    main()
