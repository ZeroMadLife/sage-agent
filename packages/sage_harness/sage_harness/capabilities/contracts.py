"""Application-neutral, browser-safe capability metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

CapabilityOrigin = Literal["local", "mcp", "skill", "subagent", "web"]
CapabilityKind = Literal["tool", "workflow", "delegate"]
CapabilitySurface = Literal["growth", "knowledge", "coding"]
CapabilityRisk = Literal["low", "medium", "high"]
CapabilityPermission = Literal["none", "approval", "runtime"]
CapabilityAvailability = Literal[
    "available",
    "degraded",
    "unavailable",
    "disabled",
    "stale",
]

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}(?::[a-z0-9][a-z0-9_.-]{0,127})+$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ORIGINS = frozenset({"local", "mcp", "skill", "subagent", "web"})
_KINDS = frozenset({"tool", "workflow", "delegate"})
_SURFACES = frozenset({"growth", "knowledge", "coding"})
_RISKS = frozenset({"low", "medium", "high"})
_PERMISSIONS = frozenset({"none", "approval", "runtime"})
_AVAILABILITY = frozenset({"available", "degraded", "unavailable", "disabled", "stale"})


def _bounded_text(label: str, value: str, *, maximum: int) -> None:
    if not value.strip() or len(value) > maximum or _CONTROL.search(value):
        raise ValueError(f"capability {label} must be non-empty, bounded, and printable")


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """One non-executable capability description safe for public catalogs."""

    capability_id: str
    name: str
    origin: CapabilityOrigin
    kind: CapabilityKind
    revision: str
    description: str
    surfaces: tuple[CapabilitySurface, ...]
    risk: CapabilityRisk
    permission: CapabilityPermission
    deferred: bool
    remote_content: bool
    availability: CapabilityAvailability
    timeout_seconds: float
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _CAPABILITY_ID.fullmatch(self.capability_id) is None:
            raise ValueError("capability id must be a stable namespaced identifier")
        _bounded_text("name", self.name, maximum=128)
        _bounded_text("revision", self.revision, maximum=128)
        _bounded_text("description", self.description, maximum=1000)
        if self.origin not in _ORIGINS:
            raise ValueError("unsupported capability origin")
        if self.kind not in _KINDS:
            raise ValueError("unsupported capability kind")
        if self.risk not in _RISKS:
            raise ValueError("unsupported capability risk")
        if self.permission not in _PERMISSIONS:
            raise ValueError("unsupported capability permission")
        if self.availability not in _AVAILABILITY:
            raise ValueError("unsupported capability availability")
        normalized_surfaces = tuple(sorted(set(self.surfaces)))
        if not normalized_surfaces or any(item not in _SURFACES for item in normalized_surfaces):
            raise ValueError("capability surfaces must be non-empty and supported")
        if not 0 < self.timeout_seconds <= 3600:
            raise ValueError("capability timeout must be positive and bounded")
        normalized_tags = tuple(sorted(set(self.tags)))
        if any(_TAG.fullmatch(item) is None for item in normalized_tags):
            raise ValueError("capability tags must be stable bounded identifiers")
        object.__setattr__(self, "surfaces", normalized_surfaces)
        object.__setattr__(self, "tags", normalized_tags)

    def as_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-safe public projection."""
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "origin": self.origin,
            "kind": self.kind,
            "revision": self.revision,
            "description": self.description,
            "surfaces": list(self.surfaces),
            "risk": self.risk,
            "permission": self.permission,
            "deferred": self.deferred,
            "remote_content": self.remote_content,
            "availability": self.availability,
            "timeout_seconds": self.timeout_seconds,
            "tags": list(self.tags),
        }


__all__ = [
    "CapabilityAvailability",
    "CapabilityDescriptor",
    "CapabilityKind",
    "CapabilityOrigin",
    "CapabilityPermission",
    "CapabilityRisk",
    "CapabilitySurface",
]
