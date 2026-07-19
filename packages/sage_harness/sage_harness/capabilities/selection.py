"""Deterministic, non-executable capability discovery and selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sage_harness.capabilities.contracts import CapabilityDescriptor, CapabilitySurface
from sage_harness.capabilities.registry import CapabilityRegistry

MAX_SELECTION_RESULTS = 5
MAX_SELECTION_QUERY_CHARS = 200
SelectionRejectionCode = Literal[
    "ambiguous",
    "disallowed",
    "not_deferred",
    "surface_mismatch",
    "unavailable",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    """Map one stable capability ID to its model-visible executable name."""

    capability_id: str
    tool_name: str

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability binding id must not be empty")
        if not self.tool_name.strip() or len(self.tool_name) > 128:
            raise ValueError("capability binding tool name must be non-empty and bounded")


@dataclass(frozen=True, slots=True)
class CapabilityMatch:
    """Public descriptor plus an optional server-owned execution binding."""

    descriptor: CapabilityDescriptor
    tool_name: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload = self.descriptor.as_dict()
        payload["promotable"] = self.tool_name is not None
        if self.tool_name is not None:
            payload["invocation"] = {"type": "tool", "name": self.tool_name}
        elif self.descriptor.kind == "workflow":
            payload["invocation"] = {
                "type": "user_command",
                "command": f"/{self.descriptor.name}",
                "requires_user_action": True,
            }
        elif self.descriptor.kind == "delegate":
            payload["invocation"] = {
                "type": "delegate",
                "tool": "task",
                "subagent_type": self.descriptor.name,
            }
        else:
            payload["invocation"] = {"type": "not_bound"}
        return payload


@dataclass(frozen=True, slots=True)
class CapabilityRejection:
    """Bounded reason why one requested identifier was not selected."""

    identifier: str
    code: SelectionRejectionCode
    candidate_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "code": self.code,
            "candidate_ids": list(self.candidate_ids),
        }


@dataclass(frozen=True, slots=True)
class CapabilitySelectionOutcome:
    """Selected public metadata and fail-closed rejection details."""

    selected: tuple[CapabilityMatch, ...] = ()
    rejected: tuple[CapabilityRejection, ...] = ()


class CapabilitySelectionIndex:
    """Search one scoped registry and resolve explicit stable selections."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        bindings: tuple[CapabilityBinding, ...] = (),
        surface: CapabilitySurface,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> None:
        self.registry = registry
        self.surface = surface
        self.allowed_tool_names = (
            frozenset(item.strip() for item in allowed_tool_names if item.strip())
            if allowed_tool_names is not None
            else None
        )
        by_id: dict[str, CapabilityBinding] = {}
        by_tool_name: dict[str, CapabilityBinding] = {}
        for binding in bindings:
            descriptor = registry.get(binding.capability_id)
            if descriptor is None:
                raise ValueError(f"unknown capability binding: {binding.capability_id}")
            if descriptor.kind != "tool" or not descriptor.deferred:
                raise ValueError(
                    f"binding requires a deferred tool capability: {binding.capability_id}"
                )
            if binding.capability_id in by_id:
                raise ValueError(f"duplicate capability binding: {binding.capability_id}")
            if binding.tool_name in by_tool_name:
                raise ValueError(f"duplicate tool binding: {binding.tool_name}")
            by_id[binding.capability_id] = binding
            by_tool_name[binding.tool_name] = binding
        self._bindings_by_id = by_id
        self._bindings_by_tool_name = by_tool_name

    @property
    def revision(self) -> str:
        return self.registry.revision

    @property
    def bindings(self) -> tuple[CapabilityBinding, ...]:
        return tuple(self._bindings_by_id[key] for key in sorted(self._bindings_by_id))

    def discover(
        self,
        query: str,
        *,
        limit: int = MAX_SELECTION_RESULTS,
    ) -> tuple[CapabilityMatch, ...]:
        """Return deterministic bounded metadata without executable schemas."""
        normalized = " ".join(str(query).casefold().split())[:MAX_SELECTION_QUERY_CHARS]
        if not normalized or limit < 1:
            return ()
        tokens = tuple(token for token in normalized.split() if token)
        ranked: list[tuple[int, str, CapabilityMatch]] = []
        for descriptor in self.registry.list():
            rejection = self._rejection(descriptor)
            if rejection is not None:
                continue
            searchable = " ".join(
                (
                    descriptor.capability_id,
                    descriptor.name,
                    descriptor.description,
                    *descriptor.tags,
                )
            ).casefold()
            if normalized not in searchable and not any(token in searchable for token in tokens):
                continue
            name = descriptor.name.casefold()
            capability_id = descriptor.capability_id.casefold()
            score = 8 if normalized == capability_id else 7 if normalized == name else 0
            score += 5 if normalized in name else 3 if normalized in searchable else 0
            score += sum(1 for token in tokens if token in searchable)
            ranked.append(
                (
                    score,
                    descriptor.capability_id,
                    CapabilityMatch(
                        descriptor,
                        self._tool_name(descriptor.capability_id),
                    ),
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item for _, _, item in ranked[: min(limit, MAX_SELECTION_RESULTS)])

    def list_discoverable(self, *, limit: int = 100) -> tuple[CapabilityMatch, ...]:
        """List scoped available entries for a schema-free prompt index."""
        if limit < 1:
            return ()
        matches = [
            CapabilityMatch(descriptor, self._tool_name(descriptor.capability_id))
            for descriptor in self.registry.list()
            if self._rejection(descriptor) is None
        ]
        return tuple(matches[: min(limit, 100)])

    def select(self, identifiers: tuple[str, ...]) -> CapabilitySelectionOutcome:
        """Resolve stable IDs, or an unambiguous legacy display/tool name."""
        selected: list[CapabilityMatch] = []
        rejected: list[CapabilityRejection] = []
        seen: set[str] = set()
        for raw_identifier in identifiers[:MAX_SELECTION_RESULTS]:
            identifier = str(raw_identifier).strip()[:MAX_SELECTION_QUERY_CHARS]
            if not identifier:
                continue
            candidates = self._candidates(identifier)
            if not candidates:
                rejected.append(CapabilityRejection(identifier, "unknown"))
                continue
            if len(candidates) > 1:
                rejected.append(
                    CapabilityRejection(
                        identifier,
                        "ambiguous",
                        tuple(item.capability_id for item in candidates[:MAX_SELECTION_RESULTS]),
                    )
                )
                continue
            descriptor = candidates[0]
            rejection = self._rejection(descriptor)
            if rejection is not None:
                rejected.append(CapabilityRejection(identifier, rejection))
                continue
            if descriptor.capability_id in seen:
                continue
            seen.add(descriptor.capability_id)
            selected.append(
                CapabilityMatch(
                    descriptor,
                    self._tool_name(descriptor.capability_id),
                )
            )
        return CapabilitySelectionOutcome(tuple(selected), tuple(rejected))

    def _candidates(self, identifier: str) -> tuple[CapabilityDescriptor, ...]:
        exact = self.registry.get(identifier)
        if exact is not None:
            return (exact,)
        folded = identifier.casefold()
        matches = [
            descriptor
            for descriptor in self.registry.list()
            if descriptor.name.casefold() == folded
            or (
                (binding := self._bindings_by_id.get(descriptor.capability_id)) is not None
                and binding.tool_name.casefold() == folded
            )
        ]
        return tuple(matches)

    def _tool_name(self, capability_id: str) -> str | None:
        binding = self._bindings_by_id.get(capability_id)
        return binding.tool_name if binding is not None else None

    def _rejection(self, descriptor: CapabilityDescriptor) -> SelectionRejectionCode | None:
        if descriptor.availability != "available":
            return "unavailable"
        if self.surface not in descriptor.surfaces:
            return "surface_mismatch"
        if not descriptor.deferred:
            return "not_deferred"
        if descriptor.kind == "tool" and self._tool_name(descriptor.capability_id) is None:
            return "unavailable"
        if self.allowed_tool_names is not None:
            tool_name = self._tool_name(descriptor.capability_id)
            if tool_name is None or tool_name not in self.allowed_tool_names:
                return "disallowed"
        return None


__all__ = [
    "CapabilityBinding",
    "CapabilityMatch",
    "CapabilityRejection",
    "CapabilitySelectionIndex",
    "CapabilitySelectionOutcome",
    "SelectionRejectionCode",
]
