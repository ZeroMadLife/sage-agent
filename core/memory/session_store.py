"""Session storage with Redis hot cache and SQL persistence."""

import inspect
import json
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.memory.compressor import ContextCompressor
from core.memory.short_term import SESSION_TTL_SECONDS
from db.models import ItineraryRecord, MessageRecord, SessionRecord, utc_now
from models.itinerary import Itinerary

Message = dict[str, str]


async def _maybe_await(value: Any) -> Any:
    """Await coroutine-like values while allowing synchronous clients."""
    if inspect.isawaitable(value):
        return await value
    return value


class SessionStore:
    """会话存储管理器 — Redis 热数据 + PostgreSQL 持久化。"""

    def __init__(
        self,
        redis_client: Any,
        db_session_factory: async_sessionmaker[AsyncSession],
        compressor: ContextCompressor | None = None,
    ) -> None:
        self._redis = redis_client
        self._session_factory = db_session_factory
        self._compressor = compressor

    async def create_session(self, user_id: str, title: str = "") -> str:
        """创建新会话，写入 PostgreSQL。"""
        session_id = str(uuid4())
        async with self._session_factory() as session:
            session.add(SessionRecord(id=session_id, user_id=user_id, title=title))
            await session.commit()
        return session_id

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """保存消息：Redis 追加+刷新 TTL，并写入 PostgreSQL。"""
        messages = await self.load_messages(session_id)
        messages.append({"role": role, "content": content})
        await self._write_redis_messages(session_id, messages)

        tool_calls_json = (
            json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
        )
        async with self._session_factory() as session:
            session.add(
                MessageRecord(
                    session_id=session_id,
                    role=role,
                    content=content,
                    tool_calls_json=tool_calls_json,
                )
            )
            record = await session.get(SessionRecord, session_id)
            if record is not None:
                record.updated_at = utc_now()
                if not record.title and role == "user":
                    record.title = content[:200]
            await session.commit()

    async def load_messages(self, session_id: str) -> list[Message]:
        """加载对话历史：Redis 优先，miss 时从 PostgreSQL 恢复并回填 Redis。"""
        cached = await self._read_redis_messages(session_id)
        if cached is not None:
            return cached

        async with self._session_factory() as session:
            result = await session.scalars(
                select(MessageRecord)
                .where(MessageRecord.session_id == session_id)
                .order_by(MessageRecord.id)
            )
            messages = [{"role": record.role, "content": record.content} for record in result.all()]

        if messages:
            await self._write_redis_messages(session_id, messages)
        return messages

    async def archive_itinerary(
        self,
        session_id: str,
        user_id: str,
        itinerary: Itinerary,
    ) -> None:
        """归档行程到 PostgreSQL。"""
        async with self._session_factory() as session:
            session.add(
                ItineraryRecord(
                    session_id=session_id,
                    user_id=user_id,
                    destination=itinerary.destination,
                    content_json=itinerary.model_dump_json(),
                    total_cost=itinerary.total_cost,
                )
            )
            await session.commit()

    async def list_sessions(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """列出用户历史会话。"""
        async with self._session_factory() as session:
            result = await session.scalars(
                select(SessionRecord)
                .where(SessionRecord.user_id == user_id)
                .order_by(SessionRecord.updated_at.desc())
                .limit(limit)
            )
            records = result.all()

        return [
            {
                "session_id": record.id,
                "title": record.title,
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
                "status": record.status,
            }
            for record in records
        ]

    async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """从 PostgreSQL 加载会话全部消息（历史回看）。"""
        async with self._session_factory() as session:
            result = await session.scalars(
                select(MessageRecord)
                .where(MessageRecord.session_id == session_id)
                .order_by(MessageRecord.id)
            )
            records = result.all()

        return [
            {
                "role": record.role,
                "content": record.content,
                "tool_calls": self._loads_tool_calls(record.tool_calls_json),
                "created_at": record.created_at.isoformat(),
            }
            for record in records
        ]

    async def list_itineraries(
        self,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出归档行程，可按用户或会话过滤。"""
        statement = select(ItineraryRecord)
        if user_id is not None:
            statement = statement.where(ItineraryRecord.user_id == user_id)
        if session_id is not None:
            statement = statement.where(ItineraryRecord.session_id == session_id)
        statement = statement.order_by(ItineraryRecord.created_at.desc())

        async with self._session_factory() as session:
            result = await session.scalars(statement)
            records = result.all()

        return [
            {
                "id": record.id,
                "destination": record.destination,
                "total_cost": record.total_cost,
                "created_at": record.created_at.isoformat(),
                "content": Itinerary.model_validate_json(record.content_json),
            }
            for record in records
        ]

    async def maybe_compress(self, session_id: str) -> bool:
        """检查并执行上下文压缩。"""
        if self._compressor is None:
            return False

        messages = await self.load_messages(session_id)
        if not messages or not self._compressor.should_compress(messages):
            return False

        compressed = await self._compressor.compress(messages)
        if compressed == messages:
            return False

        await self._write_redis_messages(session_id, compressed)
        return True

    async def _write_redis_messages(self, session_id: str, messages: list[Message]) -> None:
        payload = json.dumps({"messages": messages}, ensure_ascii=False)
        await _maybe_await(
            self._redis.setex(self._redis_key(session_id), SESSION_TTL_SECONDS, payload)
        )

    async def _read_redis_messages(self, session_id: str) -> list[Message] | None:
        raw = await _maybe_await(self._redis.get(self._redis_key(session_id)))
        if raw is None or raw == "":
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not isinstance(raw, str):
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None

        payload = data.get("messages") if isinstance(data, dict) else data
        if not isinstance(payload, list):
            return None

        messages: list[Message] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if isinstance(role, str) and isinstance(content, str):
                messages.append({"role": role, "content": content})
        return messages

    @staticmethod
    def _loads_tool_calls(value: str | None) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, list):
            return None
        return cast(list[dict[str, Any]], data)

    @staticmethod
    def _redis_key(session_id: str) -> str:
        return f"session:{session_id}:messages"
