"""Minimal Python sidecar used by the desktop packaging spike."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from desktop.sidecar.app import DESKTOP_API_VERSION, create_desktop_app

__all__ = ["DESKTOP_API_VERSION", "create_desktop_app"]


def __getattr__(name: str) -> Any:
    """Load runtime exports only when callers request them."""
    if name in __all__:
        from desktop.sidecar import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
