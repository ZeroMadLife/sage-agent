from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

ContextLevel = Literal[
    "normal",
    "budget",
    "snip",
    "compact",
    "high",
    "emergency",
]


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    estimated: bool


@dataclass(frozen=True)
class ContextUsage:
    used_tokens: int
    effective_limit_tokens: int
    usage_ratio: float
    level: ContextLevel
    estimated: bool


@dataclass(frozen=True)
class ContextPolicy:
    context_window_tokens: int
    output_reserve_tokens: int = 20_000
    budget_ratio: float = 0.50
    snip_ratio: float = 0.60
    compact_ratio: float = 0.65
    high_ratio: float = 0.70
    cache_override_ratio: float = 0.75
    emergency_ratio: float = 0.85

    def __post_init__(self) -> None:
        if self.context_window_tokens <= self.output_reserve_tokens:
            raise ValueError("context window must exceed output reserve")

        thresholds = (
            self.budget_ratio,
            self.snip_ratio,
            self.compact_ratio,
            self.high_ratio,
            self.cache_override_ratio,
            self.emergency_ratio,
        )
        in_range = all(0.0 <= threshold <= 1.0 for threshold in thresholds)
        strictly_increasing = all(lower < upper for lower, upper in pairwise(thresholds))
        if not in_range or not strictly_increasing:
            raise ValueError("thresholds must be strictly increasing within 0..1")

    @property
    def effective_limit_tokens(self) -> int:
        return self.context_window_tokens - self.output_reserve_tokens

    def usage(self, used_tokens: int, estimated: bool = False) -> ContextUsage:
        if used_tokens < 0:
            raise ValueError("used_tokens must be non-negative")

        usage_ratio = used_tokens / self.effective_limit_tokens
        level: ContextLevel
        if usage_ratio >= self.emergency_ratio:
            level = "emergency"
        elif usage_ratio >= self.high_ratio:
            level = "high"
        elif usage_ratio >= self.compact_ratio:
            level = "compact"
        elif usage_ratio >= self.snip_ratio:
            level = "snip"
        elif usage_ratio >= self.budget_ratio:
            level = "budget"
        else:
            level = "normal"

        return ContextUsage(
            used_tokens=used_tokens,
            effective_limit_tokens=self.effective_limit_tokens,
            usage_ratio=usage_ratio,
            level=level,
            estimated=estimated,
        )


class TokenCounter:
    def __init__(self, model: object | None = None) -> None:
        self.model = model

    def count(self, text: str) -> TokenCount:
        model_counter = getattr(self.model, "get_num_tokens", None)
        if callable(model_counter):
            try:
                return TokenCount(
                    tokens=max(1, int(model_counter(text))),
                    estimated=False,
                )
            except Exception:
                pass

        fallback_tokens = math.ceil(len(text.encode("utf-8")) / 4)
        return TokenCount(tokens=max(1, fallback_tokens), estimated=True)
