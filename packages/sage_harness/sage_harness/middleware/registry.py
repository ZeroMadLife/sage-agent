"""Deterministic middleware registry with explicit insertion points."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from sage_harness.config import HarnessConfig
from sage_harness.middleware.builtin import (
    InputSanitizationMiddleware,
    ProviderErrorMiddleware,
    RemoteContentSanitizationMiddleware,
    RunBudgetMiddleware,
    TerminalResponseMiddleware,
    ThreadContextMiddleware,
    ToolErrorMiddleware,
)
from sage_harness.middleware.durable_context import DurableContextMiddleware

Middleware = AgentMiddleware[Any, Any, Any]
MiddlewareFactory = Callable[[HarnessConfig], Middleware]


@dataclass(frozen=True, slots=True)
class MiddlewareSpec:
    """Named factory used to keep ordering stable and testable."""

    name: str
    factory: MiddlewareFactory

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Middleware name must not be empty")


class MiddlewareRegistry:
    """Immutable ordered middleware registry."""

    def __init__(self, specs: Sequence[MiddlewareSpec] = ()) -> None:
        names = [spec.name for spec in specs]
        if len(names) != len(set(names)):
            raise ValueError("Middleware names must be unique")
        self._specs = tuple(specs)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._specs)

    def build(self, config: HarnessConfig) -> list[Middleware]:
        return [spec.factory(config) for spec in self._specs]

    def with_spec(
        self,
        spec: MiddlewareSpec,
        *,
        before: str | None = None,
        after: str | None = None,
    ) -> MiddlewareRegistry:
        if spec.name in self.names:
            raise ValueError(f"Middleware already registered: {spec.name}")
        if before and after:
            raise ValueError("Specify either before or after, not both")

        specs = list(self._specs)
        if before or after:
            anchor = before or after
            if anchor not in self.names:
                raise ValueError(f"Unknown middleware anchor: {anchor}")
            index = self.names.index(anchor)
            if after:
                index += 1
            specs.insert(index, spec)
        else:
            specs.append(spec)
        return MiddlewareRegistry(specs)


def build_default_registry() -> MiddlewareRegistry:
    """Return the minimum safe chain in outer-to-inner execution order."""
    return MiddlewareRegistry(
        [
            MiddlewareSpec("input_sanitization", lambda config: InputSanitizationMiddleware()),
            MiddlewareSpec("thread_context", lambda config: ThreadContextMiddleware()),
            MiddlewareSpec("durable_context", lambda config: DurableContextMiddleware()),
            MiddlewareSpec("provider_error", lambda config: ProviderErrorMiddleware()),
            MiddlewareSpec(
                "remote_content_sanitization",
                lambda config: RemoteContentSanitizationMiddleware(),
            ),
            MiddlewareSpec("tool_error", lambda config: ToolErrorMiddleware()),
            MiddlewareSpec(
                "run_budget",
                lambda config: RunBudgetMiddleware(
                    max_model_calls=config.max_model_calls,
                    max_tool_calls=config.max_tool_calls,
                    max_tokens=config.max_run_tokens,
                ),
            ),
            MiddlewareSpec("terminal_response", lambda config: TerminalResponseMiddleware()),
        ]
    )


__all__ = ["MiddlewareRegistry", "MiddlewareSpec", "build_default_registry"]
