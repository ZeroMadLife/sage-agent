"""Redis Streams delivery semantics."""

from __future__ import annotations

import asyncio
from typing import Any

from core.knowledge.jobs import RedisKnowledgeJobQueue


async def test_publish_is_deduplicated_until_acknowledged(
    job_infrastructure: tuple[Any, RedisKnowledgeJobQueue, Any],
) -> None:
    _, queue, _ = job_infrastructure
    await queue.initialize()

    assert await queue.publish("item-1") is True
    assert await queue.publish("item-1") is False
    messages = await queue.read("worker-1", block_ms=1)
    assert [message.item_id for message in messages] == ["item-1"]

    await queue.acknowledge(messages[0])
    assert await queue.redis.xlen(queue.stream) == 0
    assert await queue.publish("item-1") is True


async def test_pending_delivery_can_move_to_a_restarted_worker(
    job_infrastructure: tuple[Any, RedisKnowledgeJobQueue, Any],
) -> None:
    _, queue, _ = job_infrastructure
    await queue.initialize()
    await queue.publish("item-restart")
    assert await queue.read("old-worker", block_ms=1)

    recovered = await queue.recover_pending("new-worker", min_idle_ms=0)

    assert [message.item_id for message in recovered] == ["item-restart"]
    await queue.acknowledge(recovered[0])


async def test_concurrent_publish_creates_exactly_one_delivery(
    job_infrastructure: tuple[Any, RedisKnowledgeJobQueue, Any],
) -> None:
    _, queue, _ = job_infrastructure
    await queue.initialize()

    outcomes = await asyncio.gather(queue.publish("item-race"), queue.publish("item-race"))

    assert sorted(outcomes) == [False, True]
    assert await queue.redis.xlen(queue.stream) == 1
