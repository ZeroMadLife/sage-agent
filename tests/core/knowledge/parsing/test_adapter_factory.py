"""Application wiring for optional external parsing adapters."""

import pytest

from core.config.settings import Settings
from core.knowledge.parsing.adapters import build_external_parse_coordinator


def test_external_adapter_factory_is_disabled_by_default() -> None:
    assert build_external_parse_coordinator(Settings(_env_file=None)) is None


def test_external_adapter_factory_requires_source_allowlist() -> None:
    with pytest.raises(RuntimeError, match="ALLOWED_SOURCE_IDS"):
        build_external_parse_coordinator(
            Settings(_env_file=None, knowledge_external_parsing_enabled=True)
        )


def test_external_adapter_factory_orders_mineru_before_qwen_fallback() -> None:
    coordinator = build_external_parse_coordinator(
        Settings(
            _env_file=None,
            knowledge_external_parsing_enabled=True,
            knowledge_external_allowed_source_ids="vault,public",
            knowledge_mineru_enabled=True,
            knowledge_qwen_vl_enabled=True,
            knowledge_qwen_vl_api_key="test-key",
        )
    )

    assert coordinator is not None
    assert coordinator.policy.allowed_source_ids == frozenset({"vault", "public"})
    assert [adapter.adapter_id for adapter in coordinator.adapters] == [
        "mineru.agent",
        "qwen3-vl",
    ]
