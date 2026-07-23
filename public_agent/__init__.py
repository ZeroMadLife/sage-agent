"""Restricted public-corpus Agent for the Sage public facade.

The package lifecycle controller runs on a minimal host Python installation.
Keep the FastAPI runtime lazy so importing ``public_agent.registry`` does not
pull web or provider dependencies into that root-owned control plane.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from public_agent.app import create_public_agent_app

__all__ = ["create_public_agent_app"]


def __getattr__(name: str) -> Any:
    if name == "create_public_agent_app":
        from public_agent.app import create_public_agent_app

        return create_public_agent_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
