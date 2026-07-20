"""Policy-gated orchestration for optional external document parsers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

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


@dataclass(frozen=True, slots=True)
class ExternalParseTicket:
    """Opaque server-side handle for one resumable external parse task."""

    adapter_id: str
    adapter_version: str
    task_id: str


@dataclass(frozen=True, slots=True)
class ExternalParsePending:
    """A remote task that should be polled later without holding a worker lease."""

    ticket: ExternalParseTicket
    stage: ExternalParseStage
    retry_after_seconds: float
    completed_units: int | None = None
    total_units: int | None = None


@dataclass(frozen=True, slots=True)
class ExternalParseCompleted:
    document: ParsedDocument


ExternalParseDispatch = ExternalParsePending | ExternalParseCompleted


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


@runtime_checkable
class ResumableExternalParseAdapter(Protocol):
    """An external parser whose remote wait can survive worker and process restarts."""

    async def submit(
        self,
        request: ParseRequest,
        *,
        progress: ProgressCallback,
    ) -> ExternalParsePending: ...

    async def resume(
        self,
        request: ParseRequest,
        ticket: ExternalParseTicket,
        *,
        progress: ProgressCallback,
    ) -> ExternalParseDispatch: ...


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
        outcome = await self.dispatch(source_root_id, request, progress=progress)
        while isinstance(outcome, ExternalParsePending):
            await asyncio.sleep(outcome.retry_after_seconds)
            outcome = await self.dispatch(
                source_root_id,
                request,
                ticket=outcome.ticket,
                progress=progress,
            )
        return outcome.document

    def supports(self, media_type: str) -> bool:
        return any(media_type in adapter.media_types for adapter in self.adapters)

    async def dispatch(
        self,
        source_root_id: str,
        request: ParseRequest,
        *,
        ticket: ExternalParseTicket | None = None,
        progress: ProgressCallback | None = None,
    ) -> ExternalParseDispatch:
        """Perform one bounded submit/poll step or one non-resumable adapter call."""

        self.policy.authorize(source_root_id, request)
        report = progress or _ignore_progress
        candidates = [
            adapter for adapter in self.adapters if request.media_type in adapter.media_types
        ]
        if not candidates:
            raise ExternalParsingFailedError("no external parser supports this source format")

        start_index = 0
        if ticket is not None:
            start_index = next(
                (
                    index
                    for index, adapter in enumerate(candidates)
                    if adapter.adapter_id == ticket.adapter_id
                    and adapter.adapter_version == ticket.adapter_version
                ),
                -1,
            )
            if start_index < 0:
                raise ExternalParsingFailedError("external parser ticket is no longer supported")

        failures: list[ExternalAdapterError] = []
        for index in range(start_index, len(candidates)):
            adapter = candidates[index]
            current_ticket = ticket if index == start_index else None
            await report(
                ExternalParseProgress(
                    adapter_id=adapter.adapter_id,
                    adapter_version=adapter.adapter_version,
                    stage="selected",
                )
            )
            try:
                async with asyncio.timeout(self.policy.timeout_seconds):
                    if current_ticket is not None:
                        if not isinstance(adapter, ResumableExternalParseAdapter):
                            raise ExternalAdapterError(
                                adapter.adapter_id,
                                "invalid_ticket",
                                retryable=False,
                            )
                        outcome = await adapter.resume(
                            request,
                            current_ticket,
                            progress=report,
                        )
                    elif isinstance(adapter, ResumableExternalParseAdapter):
                        outcome = await adapter.submit(request, progress=report)
                    else:
                        outcome = ExternalParseCompleted(
                            await adapter.parse(request, progress=report)
                        )
            except TimeoutError:
                failure = ExternalAdapterError(adapter.adapter_id, "timeout", retryable=True)
            except ExternalAdapterError as exc:
                failure = exc
            else:
                if isinstance(outcome, ExternalParsePending):
                    return outcome
                await report(
                    ExternalParseProgress(
                        adapter_id=adapter.adapter_id,
                        adapter_version=adapter.adapter_version,
                        stage="completed",
                    )
                )
                return outcome

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
