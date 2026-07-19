"""Typed failures used to keep the controller fail-closed."""

from __future__ import annotations


class LoopHarnessError(RuntimeError):
    """Base class for controlled harness failures."""


class LoopBlockedError(LoopHarnessError):
    """A preflight or adapter gate blocked this run."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LeaseBusyError(LoopHarnessError):
    """Another process owns a non-expired resource lease."""


class LeaseLostError(LoopHarnessError):
    """The current process no longer owns its fencing token."""
