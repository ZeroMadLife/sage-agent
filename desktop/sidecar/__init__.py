"""Minimal Python sidecar used by the desktop packaging spike."""

from desktop.sidecar.app import DESKTOP_API_VERSION, create_desktop_app

__all__ = ["DESKTOP_API_VERSION", "create_desktop_app"]
