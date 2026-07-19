"""Shared conservative token accounting for bounded web evidence."""

from __future__ import annotations

import math


def estimated_tokens(*parts: str) -> int:
    content = "\n".join(parts)
    ascii_characters = sum(character.isascii() for character in content)
    non_ascii_bytes = sum(
        len(character.encode("utf-8"))
        for character in content
        if not character.isascii()
    )
    return max(1, math.ceil(ascii_characters / 4) + math.ceil(non_ascii_bytes / 2))


def fit_excerpt(
    excerpt: str,
    *,
    token_budget: int,
    overhead: tuple[str, ...] = (),
) -> str:
    if token_budget <= estimated_tokens(*overhead, "") + 15:
        return ""
    if estimated_tokens(*overhead, excerpt) <= token_budget:
        return excerpt
    marker = "..."
    lower = 0
    upper = len(excerpt)
    while lower < upper:
        midpoint = (lower + upper + 1) // 2
        candidate = f"{excerpt[:midpoint].rstrip()}{marker}"
        if estimated_tokens(*overhead, candidate) <= token_budget:
            lower = midpoint
        else:
            upper = midpoint - 1
    clipped = excerpt[:lower].rstrip()
    return f"{clipped}{marker}" if clipped else ""


__all__ = ["estimated_tokens", "fit_excerpt"]
