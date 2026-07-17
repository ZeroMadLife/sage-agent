"""Policy-gated orchestration for optional external document parsers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from .errors import DocumentParseError
from .types import ParsedDocument, ParseRequest

ExternalParseStage = Literal[
    "selected",
    "uploading",
    "queued",
    "running",
    "fallback",
    "completed",
]


@dataclass(frozen=True, slots=True)
class ExternalParseProgress:
    """A safe progress event that never carries source content or credentials."""

    adapter_id: str
    adapter_version: str
    stage: ExternalParseStage
    completed_units: int | None = None
    total_units: int | None = None
    reason_code: str | None = None


ProgressCallback = Callable[[ExternalParseProgress], Awaitable[None]]


class ExternalParseAdapter(Protocol):
    """One optional parser capable of processing an immutable source revision."""

    adapter_id: str
    adapter_version: str
    media_types: frozenset[str]

    async def parse(
        self,
        request: ParseRequest,
        *,
        progress: ProgressCallback,
    ) -> ParsedDocument: ...


class ExternalParsePolicyError(DocumentParseError):
    """The source is not authorized for external processing."""


class ExternalAdapterError(RuntimeError):
    """A sanitized adapter failure suitable for fallback decisions."""

    def __init__(self, adapter_id: str, code: str, *, retryable: bool) -> None:
        super().__init__(f"external parser {adapter_id} failed ({code})")
        self.adapter_id = adapter_id
        self.code = code
        self.retryable = retryable


class ExternalParsingTransientError(RuntimeError):
    """Every available adapter failed, but a later retry may succeed."""


class ExternalParsingFailedError(DocumentParseError):
    """Every available adapter failed deterministically."""


@dataclass(frozen=True, slots=True)
class ExternalParsePolicy:
    """Explicit opt-in boundary for sending private sources to third parties."""

    enabled: bool = False
    allowed_source_ids: frozenset[str] = frozenset()
    max_payload_bytes: int = 10 * 1024 * 1024
    timeout_seconds: float = 180.0

    def authorize(self, source_root_id: str, request: ParseRequest) -> None:
        if not self.enabled:
            raise ExternalParsePolicyError("external document parsing is disabled")
        if source_root_id not in self.allowed_source_ids:
            raise ExternalParsePolicyError("knowledge source is not allowed for external parsing")
        if len(request.payload) > self.max_payload_bytes:
            raise ExternalParsePolicyError("source exceeds external parsing size limit")
        if _looks_sensitive_path(request.relative_path):
            raise ExternalParsePolicyError("sensitive source path cannot be processed externally")


class ExternalParseCoordinator:
    """Select adapters in order and preserve timeout/fallback semantics."""

    def __init__(
        self,
        policy: ExternalParsePolicy,
        adapters: Sequence[ExternalParseAdapter],
    ) -> None:
        self.policy = policy
        self.adapters = tuple(adapters)

    async def parse(
        self,
        source_root_id: str,
        request: ParseRequest,
        *,
        progress: ProgressCallback | None = None,
    ) -> ParsedDocument:
        self.policy.authorize(source_root_id, request)
        report = progress or _ignore_progress
        candidates = [
            adapter for adapter in self.adapters if request.media_type in adapter.media_types
        ]
        if not candidates:
            raise ExternalParsingFailedError("no external parser supports this source format")

        failures: list[ExternalAdapterError] = []
        for index, adapter in enumerate(candidates):
            await report(
                ExternalParseProgress(
                    adapter_id=adapter.adapter_id,
                    adapter_version=adapter.adapter_version,
                    stage="selected",
                )
            )
            try:
                async with asyncio.timeout(self.policy.timeout_seconds):
                    document = await adapter.parse(request, progress=report)
            except TimeoutError:
                failure = ExternalAdapterError(adapter.adapter_id, "timeout", retryable=True)
            except ExternalAdapterError as exc:
                failure = exc
            else:
                await report(
                    ExternalParseProgress(
                        adapter_id=adapter.adapter_id,
                        adapter_version=adapter.adapter_version,
                        stage="completed",
                    )
                )
                return document

            failures.append(failure)
            if index + 1 < len(candidates):
                await report(
                    ExternalParseProgress(
                        adapter_id=adapter.adapter_id,
                        adapter_version=adapter.adapter_version,
                        stage="fallback",
                        reason_code=failure.code,
                    )
                )

        codes = ",".join(failure.code for failure in failures)
        if any(failure.retryable for failure in failures):
            raise ExternalParsingTransientError(
                f"external document parsing is temporarily unavailable ({codes})"
            )
        raise ExternalParsingFailedError(f"external document parsing failed ({codes})")


async def _ignore_progress(_: ExternalParseProgress) -> None:
    return None


_SENSITIVE_PATH = re.compile(
    r"(?:^|[._-])(?:secret|secrets|credential|credentials|api[-_]?keys?|"
    r"access[-_]?tokens?|private[-_]?keys?|passwd|passwords?)(?:[._-]|$)",
    re.IGNORECASE,
)


def _looks_sensitive_path(value: str) -> bool:
    return any(part.startswith(".") or _SENSITIVE_PATH.search(part) for part in value.split("/"))
