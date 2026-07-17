"""Optional external parser adapters and their application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..external import ExternalParseAdapter, ExternalParseCoordinator, ExternalParsePolicy
from .mineru import MinerUAdapter, MinerUConfig
from .qwen_vl import QwenVlAdapter, QwenVlConfig

if TYPE_CHECKING:
    from core.config.settings import Settings


def build_external_parse_coordinator(settings: Settings) -> ExternalParseCoordinator | None:
    """Build an explicitly enabled coordinator without logging credentials."""
    if not settings.knowledge_external_parsing_enabled:
        return None
    allowed_source_ids = frozenset(
        value.strip()
        for value in settings.knowledge_external_allowed_source_ids.split(",")
        if value.strip()
    )
    if not allowed_source_ids:
        raise RuntimeError(
            "KNOWLEDGE_EXTERNAL_ALLOWED_SOURCE_IDS is required when external parsing is enabled"
        )
    adapters: list[ExternalParseAdapter] = []
    if settings.knowledge_mineru_enabled:
        adapters.append(
            MinerUAdapter(
                MinerUConfig(
                    base_url=settings.knowledge_mineru_base_url,
                    poll_seconds=settings.knowledge_mineru_poll_seconds,
                )
            )
        )
    if settings.knowledge_qwen_vl_enabled:
        if not settings.knowledge_qwen_vl_api_key:
            raise RuntimeError(
                "KNOWLEDGE_QWEN_VL_API_KEY is required when Qwen3-VL parsing is enabled"
            )
        adapters.append(
            QwenVlAdapter(
                QwenVlConfig(
                    api_key=settings.knowledge_qwen_vl_api_key,
                    base_url=settings.knowledge_qwen_vl_base_url,
                    model=settings.knowledge_qwen_vl_model,
                    max_pages=settings.knowledge_qwen_vl_max_pages,
                )
            )
        )
    if not adapters:
        raise RuntimeError("external parsing is enabled but no adapter is configured")
    return ExternalParseCoordinator(
        ExternalParsePolicy(
            enabled=True,
            allowed_source_ids=allowed_source_ids,
            timeout_seconds=settings.knowledge_external_timeout_seconds,
        ),
        adapters,
    )


__all__ = [
    "MinerUAdapter",
    "MinerUConfig",
    "QwenVlAdapter",
    "QwenVlConfig",
    "build_external_parse_coordinator",
]
