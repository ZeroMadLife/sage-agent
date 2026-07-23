"""Durable, content-free coding model usage accounting."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_usage (
    request_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    cache_read_tokens INTEGER CHECK (cache_read_tokens IS NULL OR cache_read_tokens >= 0),
    cache_creation_tokens INTEGER CHECK (
        cache_creation_tokens IS NULL OR cache_creation_tokens >= 0
    )
);
CREATE INDEX IF NOT EXISTS idx_model_usage_occurred_at
ON model_usage(occurred_at);
CREATE INDEX IF NOT EXISTS idx_model_usage_model_occurred_at
ON model_usage(model, occurred_at);
"""


@dataclass(frozen=True, slots=True)
class UsageSample:
    """Normalized token fields from one real Provider response."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


def normalize_usage(response: object) -> UsageSample | None:
    """Normalize known LangChain/OpenAI/Anthropic usage metadata shapes."""
    usage = getattr(response, "usage_metadata", None)
    response_metadata = getattr(response, "response_metadata", None)
    if not isinstance(response_metadata, Mapping):
        response_metadata = {}

    if isinstance(usage, Mapping) and usage:
        sample = _sample_from_usage_metadata(usage)
        if sample is not None:
            return sample

    for key in ("token_usage", "usage"):
        nested = response_metadata.get(key)
        if isinstance(nested, Mapping) and nested:
            sample = _sample_from_provider_usage(nested)
            if sample is not None:
                return sample
    return None


def _sample_from_usage_metadata(value: Mapping[object, object]) -> UsageSample | None:
    details = value.get("input_token_details")
    details = details if isinstance(details, Mapping) else {}
    return _build_sample(
        input_tokens=_optional_count(value.get("input_tokens")),
        output_tokens=_optional_count(value.get("output_tokens")),
        total_tokens=_optional_count(value.get("total_tokens")),
        cache_read_tokens=_optional_count(details.get("cache_read", details.get("cached_tokens"))),
        cache_creation_tokens=_optional_count(details.get("cache_creation")),
    )


def _sample_from_provider_usage(value: Mapping[object, object]) -> UsageSample | None:
    prompt_details = value.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, Mapping) else {}
    return _build_sample(
        input_tokens=_optional_count(value.get("input_tokens", value.get("prompt_tokens"))),
        output_tokens=_optional_count(value.get("output_tokens", value.get("completion_tokens"))),
        total_tokens=_optional_count(value.get("total_tokens")),
        cache_read_tokens=_optional_count(
            value.get(
                "cache_read_input_tokens",
                prompt_details.get("cached_tokens"),
            )
        ),
        cache_creation_tokens=_optional_count(value.get("cache_creation_input_tokens")),
    )


def _build_sample(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    cache_read_tokens: int | None,
    cache_creation_tokens: int | None,
) -> UsageSample | None:
    values = (
        input_tokens,
        output_tokens,
        total_tokens,
        cache_read_tokens,
        cache_creation_tokens,
    )
    if all(value is None for value in values):
        return None
    return UsageSample(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )


def _optional_count(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


class UsageStore:
    """SQLite ledger keyed by a deterministic model-request id."""

    def __init__(self, path: str | Path) -> None:
        expanded = Path(path).expanduser()
        self.path = expanded if expanded.is_absolute() else Path.cwd() / expanded
        self._initialized = False
        self._initialize_lock = Lock()
        self._validate_path()

    def record(
        self,
        *,
        request_id: str,
        session_id: str,
        run_id: str,
        provider: str,
        model: str,
        usage: UsageSample,
        occurred_at: datetime | None = None,
    ) -> bool:
        timestamp = _utc(occurred_at or datetime.now(UTC)).isoformat()
        self._ensure_ready()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO model_usage (
                    request_id, session_id, run_id, provider, model, occurred_at,
                    input_tokens, output_tokens, total_tokens,
                    cache_read_tokens, cache_creation_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _bounded_id(request_id, "request_id"),
                    _bounded_id(session_id, "session_id"),
                    _bounded_id(run_id, "run_id"),
                    _bounded_id(provider, "provider"),
                    _bounded_id(model, "model", limit=192),
                    timestamp,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                    usage.cache_read_tokens,
                    usage.cache_creation_tokens,
                ),
            )
            connection.commit()
            return cursor.rowcount == 1

    def summary(
        self,
        *,
        days: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if days <= 0 or days > 3650:
            raise ValueError("usage range must be between 1 and 3650 days")
        self._validate_path()
        if not self.path.exists():
            return _empty_summary(days)
        self._ensure_ready()
        reference = _utc(now or datetime.now(UTC))
        cutoff = (reference - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM model_usage WHERE occurred_at >= ? ORDER BY occurred_at",
                (cutoff,),
            ).fetchall()
        if not rows:
            return _empty_summary(days)

        input_tokens = _sum_known(rows, "input_tokens")
        output_tokens = _sum_known(rows, "output_tokens")
        total_tokens = _sum_known(rows, "total_tokens")
        cache_read_tokens = _sum_known(rows, "cache_read_tokens")
        cache_creation_tokens = _sum_known(rows, "cache_creation_tokens")
        cache_hit_ratio: float | None = None
        if input_tokens is not None and input_tokens > 0 and cache_read_tokens is not None:
            cache_hit_ratio = min(1.0, cache_read_tokens / input_tokens)

        return {
            "range_days": days,
            "request_count": len(rows),
            "session_count": len({str(row["session_id"]) for row in rows}),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_hit_ratio": cache_hit_ratio,
            "cost": None,
            "models": _group_rows(rows, "model"),
            "daily": _group_rows(rows, "date"),
        }

    def _validate_path(self) -> None:
        if self.path.is_symlink():
            raise ValueError("usage database must not be a symlink")
        if self.path.parent.exists() and self.path.parent.is_symlink():
            raise ValueError("usage database directory must not be a symlink")

    def _ensure_ready(self) -> None:
        with self._initialize_lock:
            if self._initialized:
                return
            self._validate_path()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._validate_path()
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
                connection.commit()
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        self._validate_path()
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection


def _group_rows(rows: list[sqlite3.Row], key: str) -> list[dict[str, object]]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        group_key = str(row["model"])
        if key == "date":
            group_key = str(row["occurred_at"])[:10]
        grouped.setdefault(group_key, []).append(row)
    output: list[dict[str, object]] = []
    for group_key in sorted(grouped):
        values = grouped[group_key]
        output.append(
            {
                key: group_key,
                "input_tokens": _sum_known(values, "input_tokens"),
                "output_tokens": _sum_known(values, "output_tokens"),
                "cache_read_tokens": _sum_known(values, "cache_read_tokens"),
                "total_tokens": _sum_known(values, "total_tokens"),
            }
        )
    return output


def _empty_summary(days: int) -> dict[str, Any]:
    return {
        "range_days": days,
        "request_count": 0,
        "session_count": 0,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cache_read_tokens": None,
        "cache_creation_tokens": None,
        "cache_hit_ratio": None,
        "cost": None,
        "models": [],
        "daily": [],
    }


def _sum_known(rows: list[sqlite3.Row], field: str) -> int | None:
    values = [row[field] for row in rows if row[field] is not None]
    return sum(int(value) for value in values) if values else None


def _bounded_id(value: str, field: str, *, limit: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{field} must be between 1 and {limit} characters")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} contains control characters")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
