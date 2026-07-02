"""Short-term session memory backed by Redis-style clients."""

import inspect
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 1800
MAX_TURNS = 20
KEEP_RECENT_TURNS = 4


async def _maybe_await(value: Any) -> Any:
    """Await coroutine-like values while allowing synchronous clients."""
    if inspect.isawaitable(value):
        return await value
    return value


class ShortTermMemory:
    """Redis-backed short-term session memory with sliding-window compression."""

    def __init__(
        self,
        redis_client: Any,
        session_id: str,
        max_turns: int = MAX_TURNS,
    ) -> None:
        self._redis = redis_client
        self._session_id = session_id
        self._max_turns = max_turns
        self._key = f"session:{session_id}:messages"

    @property
    def key(self) -> str:
        """Return the Redis key used for this session."""
        return self._key

    async def save_message(self, role: str, content: str) -> None:
        """Append one message and refresh the session TTL."""
        messages = await self.load_messages()
        messages.append({"role": role, "content": content})
        payload = json.dumps(messages, ensure_ascii=False)
        await _maybe_await(self._redis.setex(self._key, SESSION_TTL_SECONDS, payload))

    async def load_messages(self) -> list[dict[str, str]]:
        """Load all messages for the current session."""
        raw = await _maybe_await(self._redis.get(self._key))
        if raw is None or raw == "":
            return []
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not isinstance(raw, str):
            logger.warning("Unexpected Redis payload type for %s: %s", self._session_id, type(raw))
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to decode messages for %s: %s", self._session_id, exc)
            return []

        if not isinstance(data, list):
            return []

        messages: list[dict[str, str]] = []
        for item in data:
            if isinstance(item, dict):
                role = item.get("role")
                content = item.get("content")
                if isinstance(role, str) and isinstance(content, str):
                    messages.append({"role": role, "content": content})
        return messages

    async def clear(self) -> None:
        """Delete the session memory key."""
        await _maybe_await(self._redis.delete(self._key))

    def should_summarize(self, messages: list[dict[str, str]]) -> bool:
        """Return whether the message list has exceeded the configured turn limit."""
        return len(messages) > self._max_turns * 2

    async def summarize(
        self,
        messages: list[dict[str, str]],
        llm: Any,
    ) -> list[dict[str, str]]:
        """Summarize older messages while preserving system prompts and recent turns."""
        keep_count = KEEP_RECENT_TURNS * 2
        if len(messages) <= keep_count:
            return messages

        system_messages = [message for message in messages if message.get("role") == "system"]
        non_system_messages = [message for message in messages if message.get("role") != "system"]
        if len(non_system_messages) <= keep_count:
            return messages

        old_messages = non_system_messages[:-keep_count]
        recent_messages = non_system_messages[-keep_count:]
        conversation_text = "\n".join(
            f"{message['role']}: {message['content']}" for message in old_messages
        )
        summary_prompt = (
            "请将以下对话历史压缩为一条简洁摘要，保留用户偏好、预算、目的地、"
            "已确认行程参数和重要决策。\n\n"
            f"{conversation_text}\n\n只输出摘要内容。"
        )

        try:
            response = await llm.ainvoke([{"role": "user", "content": summary_prompt}])
            summary_content = getattr(response, "content", response)
            summary_text = (
                summary_content if isinstance(summary_content, str) else str(summary_content)
            )
        except Exception as exc:
            logger.warning("Summary compression failed for %s: %s", self._session_id, exc)
            return messages

        summary_message = {"role": "system", "content": f"[对话摘要] {summary_text}"}
        return [*system_messages, summary_message, *recent_messages]
