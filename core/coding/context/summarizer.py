"""Strict model adapter for structured context summaries."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any

from core.coding.context.summary import CompactionSummary

_FENCE = re.compile(r"\A```(?:json)?\s*\n?(.*?)\n?```\s*\Z", re.DOTALL | re.IGNORECASE)
_INSTRUCTION = "Return exactly one JSON object matching the compaction summary schema."
_MAX_ARCHIVED_BYTES = 1024 * 1024
_MAX_RAW_OUTPUT_BYTES = 256 * 1024
_MAX_SUMMARY_TOKENS = 100_000


class StructuredSummarizer:
    """Call one model with a canonical request and accept JSON only."""

    def __init__(self, model: object, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def summarize(
        self,
        *,
        archived_history: list[dict[str, Any]],
        previous_summary: CompactionSummary | None,
        focus: str,
        max_tokens: int,
        source_transcript_range: tuple[int, int],
        repair_feedback: str | None,
    ) -> Mapping[str, Any]:
        if (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or not 0 < max_tokens <= _MAX_SUMMARY_TOKENS
        ):
            raise ValueError("max_tokens is outside the supported range")
        archived_payload = json.dumps(
            archived_history,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        if len(archived_payload) > _MAX_ARCHIVED_BYTES:
            raise ValueError("archived history exceeds the size limit")
        request = {
            "archived": archived_history,
            "focus": focus,
            "max_tokens": max_tokens,
            "previous": (
                previous_summary.model_dump(mode="json") if previous_summary is not None else None
            ),
            "range": list(source_transcript_range),
            "repair": repair_feedback,
        }
        payload = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        try:
            raw = await asyncio.wait_for(
                self._invoke(f"{_INSTRUCTION}\n{payload}", max_tokens=max_tokens),
                timeout=self.timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise TimeoutError("structured summarization timed out") from None
        except Exception:
            raise RuntimeError("structured summarization failed") from None
        output_limit = min(_MAX_RAW_OUTPUT_BYTES, max(4096, max_tokens * 16))
        if len(raw.encode("utf-8")) > output_limit:
            raise ValueError("structured summary output exceeds the size limit")
        return _parse_single_object(raw)

    async def _invoke(self, prompt: str, *, max_tokens: int) -> str:
        complete = getattr(self.model, "complete", None)
        ainvoke = getattr(self.model, "ainvoke", None)
        if callable(complete):
            response = await complete(prompt, max_tokens=max_tokens)
        elif callable(ainvoke):
            response = await ainvoke(prompt, max_tokens=max_tokens)
        else:
            raise TypeError("model has no supported async completion method")
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            raise TypeError("model response content must be text")
        return content


def _parse_single_object(raw: str) -> Mapping[str, Any]:
    candidate = raw.strip()
    match = _FENCE.fullmatch(candidate)
    if match is not None:
        candidate = match.group(1).strip()
    try:
        value, end = json.JSONDecoder(object_pairs_hook=_unique_object).raw_decode(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("summary must contain exactly one JSON object") from exc
    if candidate[end:].strip() or not isinstance(value, dict):
        raise ValueError("summary must contain exactly one JSON object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
