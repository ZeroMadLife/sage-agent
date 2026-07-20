"""Application-neutral sandbox contracts for harness tool backends."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

SandboxOperation = Literal[
    "list_files",
    "read_file",
    "search",
    "write_file",
    "patch_file",
    "run_shell",
]


class SandboxPolicyError(RuntimeError):
    """Raised when a sandbox capability is unavailable by server policy."""


@dataclass(frozen=True, slots=True)
class SandboxCapabilities:
    """Public capability flags; these never imply approval has been granted."""

    isolated: bool
    host_access: bool
    read_files: bool = True
    write_files: bool = False
    shell: bool = False

    def __post_init__(self) -> None:
        if self.isolated and self.host_access:
            raise ValueError("an isolated sandbox cannot advertise host access")


@dataclass(frozen=True, slots=True)
class SandboxDescriptor:
    """Sanitized sandbox identity safe for checkpoint state and public events."""

    sandbox_id: str
    provider: str
    workspace_id: str
    capabilities: SandboxCapabilities

    def __post_init__(self) -> None:
        for name, value in (
            ("sandbox_id", self.sandbox_id),
            ("provider", self.provider),
            ("workspace_id", self.workspace_id),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Bounded result returned by a concrete sandbox adapter."""

    operation: SandboxOperation
    content: str
    is_error: bool = False
    error_code: str | None = None
    retryable: bool | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


class SandboxPort(Protocol):
    """Minimal lifecycle and invocation contract shared by local/container backends.

    Policy and approval remain application-owned. A caller must authorize a
    write or shell request before invoking the corresponding sandbox operation.
    """

    @property
    def descriptor(self) -> SandboxDescriptor: ...

    async def invoke(
        self,
        operation: SandboxOperation,
        arguments: Mapping[str, object],
    ) -> SandboxResult: ...

    async def aclose(self) -> None: ...


__all__ = [
    "SandboxCapabilities",
    "SandboxDescriptor",
    "SandboxOperation",
    "SandboxPolicyError",
    "SandboxPort",
    "SandboxResult",
]
