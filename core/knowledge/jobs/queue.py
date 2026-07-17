"""Redis Streams delivery adapter for knowledge ingestion items."""

from __future__ import annotations

from typing import Any

from redis.exceptions import WatchError

from .types import QueueMessage


class RedisKnowledgeJobQueue:
    """Use Redis only for delivery; PostgreSQL remains the state authority."""

    def __init__(
        self,
        redis: Any,
        *,
        stream: str = "sage:knowledge:ingest",
        group: str = "sage-knowledge-workers",
    ) -> None:
        self.redis = redis
        self.stream = stream
        self.group = group
        self._dedupe_prefix = f"{stream}:published:"

    async def initialize(self) -> None:
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(self, item_id: str) -> bool:
        marker = f"{self._dedupe_prefix}{item_id}"
        while True:
            try:
                async with self.redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(marker)
                    if await pipe.exists(marker):
                        return False
                    pipe.multi()
                    pipe.set(marker, "1", ex=3600)
                    pipe.xadd(self.stream, {"item_id": item_id})
                    await pipe.execute()
                    return True
            except WatchError:
                continue

    async def read(
        self, consumer: str, *, count: int = 10, block_ms: int = 500
    ) -> list[QueueMessage]:
        response = await self.redis.xreadgroup(
            self.group,
            consumer,
            {self.stream: ">"},
            count=count,
            block=block_ms,
        )
        return _decode_stream_response(response)

    async def recover_pending(
        self, consumer: str, *, min_idle_ms: int, count: int = 100
    ) -> list[QueueMessage]:
        response = await self.redis.xautoclaim(
            self.stream,
            self.group,
            consumer,
            min_idle_ms,
            "0-0",
            count=count,
        )
        entries = response[1] if response and len(response) > 1 else []
        return _decode_entries(entries)

    async def acknowledge(self, message: QueueMessage) -> None:
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xack(self.stream, self.group, message.message_id)
            pipe.xdel(self.stream, message.message_id)
            pipe.delete(f"{self._dedupe_prefix}{message.item_id}")
            await pipe.execute()


def _decode_stream_response(response: Any) -> list[QueueMessage]:
    messages: list[QueueMessage] = []
    for _, entries in response or []:
        messages.extend(_decode_entries(entries))
    return messages


def _decode_entries(entries: Any) -> list[QueueMessage]:
    messages: list[QueueMessage] = []
    for message_id, fields in entries or []:
        raw_item_id = fields.get(b"item_id", fields.get("item_id", ""))
        item_id = _text(raw_item_id)
        if item_id:
            messages.append(QueueMessage(_text(message_id), item_id))
    return messages


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
