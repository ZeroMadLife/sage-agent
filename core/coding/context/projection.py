"""Pure, immutable projections of coding-session history."""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from copy import deepcopy
from typing import Any, Literal

ContextLevel = Literal["normal", "budget", "snip", "compact", "high", "emergency"]

_READ_TOOLS = frozenset({"read_file", "search", "list_files"})
_DEDUP_LEVELS = frozenset({"snip", "compact", "high", "emergency"})
_OUTPUT_CAPS: dict[ContextLevel, int] = {
    "normal": 50_000,
    "budget": 30_000,
    "snip": 30_000,
    "compact": 30_000,
    "high": 15_000,
    "emergency": 15_000,
}


class ContextProjector:
    """Return a bounded view of history without mutating canonical evidence."""

    def project(
        self,
        history: list[dict[str, Any]],
        level: ContextLevel,
    ) -> list[dict[str, Any]]:
        projected = deepcopy(history)
        cap = _OUTPUT_CAPS[level]
        tool_indexes = [index for index, item in enumerate(projected) if item.get("role") == "tool"]
        protected = set(tool_indexes[-3:])
        seen_reads: set[tuple[str, Hashable]] = set()

        for index in reversed(tool_indexes):
            item = projected[index]
            name = str(item.get("name", ""))
            args = item.get("args")
            signature = _tool_signature(name, args)
            duplicate_read = (
                name in _READ_TOOLS and signature is not None and signature in seen_reads
            )

            if level in _DEDUP_LEVELS and index not in protected and duplicate_read:
                artifact_ref = str(item.get("artifact_ref", "")) or "unavailable"
                item["content"] = f"[older duplicate result removed; artifact_ref={artifact_ref}]"
            else:
                if name in _READ_TOOLS and signature is not None:
                    seen_reads.add(signature)
                item["content"] = _bounded_preview(
                    str(item.get("content", "")),
                    cap,
                    str(item.get("artifact_ref", "")),
                )
        return projected


def _tool_signature(name: str, args: Any) -> tuple[str, Hashable] | None:
    """Return a stable, type-preserving signature for complete tool arguments."""
    try:
        frozen_args = _freeze(args)
    except TypeError:
        return None
    return name, frozen_args


def _freeze(value: Any) -> Hashable:
    """Recursively freeze supported argument values without erasing their types."""
    if value is None:
        return ("none",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value.hex())
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, bytes):
        return ("bytes", value)
    if isinstance(value, Mapping):
        items = [(_freeze(key), _freeze(item)) for key, item in value.items()]
        items.sort(key=lambda pair: repr(pair[0]))
        return ("mapping", tuple(items))
    if isinstance(value, list):
        return ("list", tuple(_freeze(item) for item in value))
    if isinstance(value, tuple):
        return ("tuple", tuple(_freeze(item) for item in value))
    raise TypeError(f"unsupported tool argument type: {type(value).__name__}")


def _bounded_preview(content: str, cap: int, artifact_ref: str) -> str:
    if len(content) <= cap:
        return content
    reference = artifact_ref or "unavailable"
    marker = f"\n...[tool output truncated; artifact_ref={reference}]...\n"
    available = cap - len(marker)
    if available <= 0:
        return marker[:cap]
    head = (available + 1) // 2
    tail = available - head
    return marker.join((content[:head], content[-tail:] if tail else ""))
