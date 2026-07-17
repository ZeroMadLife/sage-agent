"""External parsing policy, timeout, and fallback coverage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from core.knowledge.parsing import (
    ExternalAdapterError,
    ExternalParseCoordinator,
    ExternalParsePolicy,
    ExternalParsePolicyError,
    ExternalParseProgress,
    ExternalParsingTransientError,
    ParsedBlock,
    ParsedDocument,
    ParseProvenance,
    ParseRequest,
)


def _request(path: str = "reports/scan.pdf") -> ParseRequest:
    return ParseRequest(
        source_id="src_123",
        relative_path=path,
        source_revision="sha256:123",
        media_type="application/pdf",
        payload=b"%PDF scan",
    )


def _document(request: ParseRequest, adapter_id: str) -> ParsedDocument:
    return ParsedDocument(
        document_id="pdoc_external",
        source_id=request.source_id,
        relative_path=request.relative_path,
        source_revision=request.source_revision,
        title="Scanned report",
        language="zh",
        rendered_markdown="# Scanned report\n\nRecovered text.\n",
        blocks=(
            ParsedBlock(
                block_id="pblk_external",
                ordinal=0,
                kind="paragraph",
                text="Recovered text.",
                heading_path=("Scanned report",),
                page=1,
                confidence=0.9,
            ),
        ),
        provenance=ParseProvenance(
            parser_id=adapter_id,
            parser_version="1.0.0",
            input_revision=request.source_revision,
            media_type=request.media_type,
        ),
    )


@dataclass
class _Adapter:
    adapter_id: str
    outcome: str = "success"
    adapter_version: str = "1.0.0"
    media_types: frozenset[str] = frozenset({"application/pdf"})

    async def parse(self, request: ParseRequest, *, progress: object) -> ParsedDocument:
        if self.outcome == "timeout":
            await asyncio.sleep(0.1)
        if self.outcome == "temporary":
            raise ExternalAdapterError(self.adapter_id, "upstream_503", retryable=True)
        if self.outcome == "invalid":
            raise ExternalAdapterError(self.adapter_id, "invalid_result", retryable=False)
        return _document(request, self.adapter_id)


async def test_external_parsing_is_disabled_and_source_scoped_by_default() -> None:
    request = _request()
    disabled = ExternalParseCoordinator(ExternalParsePolicy(), [_Adapter("mineru")])
    with pytest.raises(ExternalParsePolicyError, match="disabled"):
        await disabled.parse("vault", request)

    scoped = ExternalParseCoordinator(
        ExternalParsePolicy(enabled=True, allowed_source_ids=frozenset({"public"})),
        [_Adapter("mineru")],
    )
    with pytest.raises(ExternalParsePolicyError, match="not allowed"):
        await scoped.parse("vault", request)
    with pytest.raises(ExternalParsePolicyError, match="sensitive source path"):
        await ExternalParseCoordinator(
            ExternalParsePolicy(enabled=True, allowed_source_ids=frozenset({"vault"})),
            [_Adapter("mineru")],
        ).parse("vault", _request("private/api_keys.pdf"))


async def test_external_coordinator_falls_back_without_exposing_adapter_errors() -> None:
    events: list[ExternalParseProgress] = []

    async def report(event: ExternalParseProgress) -> None:
        events.append(event)

    coordinator = ExternalParseCoordinator(
        ExternalParsePolicy(enabled=True, allowed_source_ids=frozenset({"vault"})),
        [_Adapter("mineru", "invalid"), _Adapter("qwen3-vl")],
    )

    document = await coordinator.parse("vault", _request(), progress=report)

    assert document.provenance.parser_id == "qwen3-vl"
    assert [(event.adapter_id, event.stage) for event in events] == [
        ("mineru", "selected"),
        ("mineru", "fallback"),
        ("qwen3-vl", "selected"),
        ("qwen3-vl", "completed"),
    ]
    assert events[1].reason_code == "invalid_result"


async def test_external_timeout_is_retryable_after_fallbacks_are_exhausted() -> None:
    coordinator = ExternalParseCoordinator(
        ExternalParsePolicy(
            enabled=True,
            allowed_source_ids=frozenset({"vault"}),
            timeout_seconds=0.01,
        ),
        [_Adapter("mineru", "timeout")],
    )

    with pytest.raises(ExternalParsingTransientError, match="temporarily unavailable"):
        await coordinator.parse("vault", _request())
