"""Deterministic middleware assembly contracts."""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from sage_harness.config import HarnessConfig
from sage_harness.middleware.registry import MiddlewareSpec, build_default_registry


class ExtraMiddleware(AgentMiddleware):
    """Test middleware used to verify insertion semantics."""


def test_default_middleware_order_is_stable() -> None:
    registry = build_default_registry()

    assert registry.names == (
        "input_sanitization",
        "thread_context",
        "durable_context",
        "provider_error",
        "remote_content_sanitization",
        "tool_error",
        "run_budget",
        "terminal_response",
    )
    assert [middleware.name for middleware in registry.build(HarnessConfig())] == [
        "InputSanitizationMiddleware",
        "ThreadContextMiddleware",
        "DurableContextMiddleware",
        "ProviderErrorMiddleware",
        "RemoteContentSanitizationMiddleware",
        "ToolErrorMiddleware",
        "RunBudgetMiddleware",
        "TerminalResponseMiddleware",
    ]


def test_registry_inserts_extensions_relative_to_a_named_anchor() -> None:
    registry = build_default_registry().with_spec(
        MiddlewareSpec("extra", lambda config: ExtraMiddleware()),
        before="terminal_response",
    )

    assert registry.names[-2:] == ("extra", "terminal_response")
