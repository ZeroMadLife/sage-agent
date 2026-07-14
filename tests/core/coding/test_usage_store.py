"""True Provider usage normalization and persistence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.coding.usage_store import UsageStore, normalize_usage


class UsageMessage:
    def __init__(self, *, usage_metadata=None, response_metadata=None) -> None:
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata or {}


def test_normalize_langchain_usage_metadata_with_cache_details() -> None:
    sample = normalize_usage(
        UsageMessage(
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "input_token_details": {"cache_read": 80, "cache_creation": 10},
            }
        )
    )

    assert sample is not None
    assert sample.input_tokens == 120
    assert sample.output_tokens == 30
    assert sample.total_tokens == 150
    assert sample.cache_read_tokens == 80
    assert sample.cache_creation_tokens == 10


def test_normalize_openai_and_anthropic_response_metadata() -> None:
    openai = normalize_usage(
        UsageMessage(
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": 40,
                    "total_tokens": 240,
                    "prompt_tokens_details": {"cached_tokens": 75},
                }
            }
        )
    )
    anthropic = normalize_usage(
        UsageMessage(
            response_metadata={
                "usage": {
                    "input_tokens": 90,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 55,
                    "cache_creation_input_tokens": 5,
                }
            }
        )
    )

    assert openai is not None
    assert (openai.input_tokens, openai.output_tokens, openai.cache_read_tokens) == (200, 40, 75)
    assert anthropic is not None
    assert (
        anthropic.input_tokens,
        anthropic.output_tokens,
        anthropic.cache_read_tokens,
        anthropic.cache_creation_tokens,
    ) == (90, 10, 55, 5)


def test_missing_or_empty_usage_is_not_fabricated() -> None:
    assert normalize_usage(UsageMessage()) is None
    assert normalize_usage(UsageMessage(usage_metadata={})) is None
    assert normalize_usage(UsageMessage(usage_metadata={"input_tokens": -1})) is None


def test_usage_store_is_idempotent_and_aggregates_by_day_and_model(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / ".sage" / "usage.sqlite3")
    occurred_at = datetime(2026, 7, 14, 8, 30, tzinfo=UTC)
    sample = normalize_usage(
        UsageMessage(
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "input_token_details": {"cache_read": 60},
            }
        )
    )
    assert sample is not None

    assert store.record(
        request_id="run-1:1",
        session_id="session-1",
        run_id="run-1",
        provider="openai",
        model="openai:gpt-test",
        usage=sample,
        occurred_at=occurred_at,
    ) is True
    assert store.record(
        request_id="run-1:1",
        session_id="session-1",
        run_id="run-1",
        provider="openai",
        model="openai:gpt-test",
        usage=sample,
        occurred_at=occurred_at,
    ) is False

    summary = store.summary(days=3650, now=datetime(2026, 7, 14, 12, tzinfo=UTC))

    assert summary["input_tokens"] == 120
    assert summary["output_tokens"] == 30
    assert summary["total_tokens"] == 150
    assert summary["cache_read_tokens"] == 60
    assert summary["session_count"] == 1
    assert summary["request_count"] == 1
    assert summary["cost"] is None
    assert summary["models"] == [
        {
            "model": "openai:gpt-test",
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_tokens": 60,
            "total_tokens": 150,
        }
    ]
    assert summary["daily"] == [
        {
            "date": "2026-07-14",
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_tokens": 60,
            "total_tokens": 150,
        }
    ]


def test_empty_usage_summary_returns_null_metrics(tmp_path: Path) -> None:
    store_path = tmp_path / ".sage" / "usage.sqlite3"
    store = UsageStore(store_path)

    assert not store_path.parent.exists()

    summary = store.summary(days=30)

    assert summary["input_tokens"] is None
    assert summary["output_tokens"] is None
    assert summary["total_tokens"] is None
    assert summary["cache_hit_ratio"] is None
    assert summary["cost"] is None
    assert summary["models"] == []
    assert summary["daily"] == []
    assert not store_path.parent.exists()


def test_usage_store_rejects_symlink_database(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite3"
    target.touch()
    linked = tmp_path / "usage.sqlite3"
    linked.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        UsageStore(linked)


def test_usage_store_rechecks_symlink_after_initialization(tmp_path: Path) -> None:
    store_path = tmp_path / "usage.sqlite3"
    store = UsageStore(store_path)
    sample = normalize_usage(UsageMessage(usage_metadata={"input_tokens": 1}))
    assert sample is not None
    assert store.record(
        request_id="run-1:1",
        session_id="session-1",
        run_id="run-1",
        provider="openai",
        model="openai:test",
        usage=sample,
    )
    store_path.unlink()
    target = tmp_path / "outside.sqlite3"
    target.touch()
    store_path.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        store.record(
            request_id="run-2:1",
            session_id="session-1",
            run_id="run-2",
            provider="openai",
            model="openai:test",
            usage=sample,
        )
