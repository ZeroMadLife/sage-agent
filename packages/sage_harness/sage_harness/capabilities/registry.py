"""Deterministic read-only registry for capability metadata."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from sage_harness.capabilities.contracts import (
    CapabilityAvailability,
    CapabilityDescriptor,
    CapabilityOrigin,
    CapabilitySurface,
)


class CapabilityRegistry:
    """Store public descriptors without owning discovery or execution."""

    def __init__(self, descriptors: Iterable[CapabilityDescriptor] = ()) -> None:
        self._descriptors: dict[str, CapabilityDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: CapabilityDescriptor) -> None:
        """Register idempotently and fail closed on a stable-ID conflict."""
        existing = self._descriptors.get(descriptor.capability_id)
        if existing is not None and existing != descriptor:
            raise ValueError(f"conflicting capability id: {descriptor.capability_id}")
        self._descriptors[descriptor.capability_id] = descriptor

    def get(self, capability_id: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(capability_id)

    def list(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(self._descriptors[key] for key in sorted(self._descriptors))

    def query(
        self,
        *,
        surface: CapabilitySurface | None = None,
        origins: frozenset[CapabilityOrigin] | None = None,
        availability: frozenset[CapabilityAvailability] | None = None,
        text: str = "",
    ) -> tuple[CapabilityDescriptor, ...]:
        """Return a deterministic bounded-metadata match set."""
        needle = " ".join(text.lower().split())[:200]
        matches: list[CapabilityDescriptor] = []
        for descriptor in self.list():
            if surface is not None and surface not in descriptor.surfaces:
                continue
            if origins is not None and descriptor.origin not in origins:
                continue
            if availability is not None and descriptor.availability not in availability:
                continue
            haystack = " ".join(
                (
                    descriptor.capability_id,
                    descriptor.name,
                    descriptor.description,
                    *descriptor.tags,
                )
            ).lower()
            if needle and needle not in haystack:
                continue
            matches.append(descriptor)
        return tuple(matches)

    @property
    def revision(self) -> str:
        payload = [descriptor.as_dict() for descriptor in self.list()]
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


__all__ = ["CapabilityRegistry"]
