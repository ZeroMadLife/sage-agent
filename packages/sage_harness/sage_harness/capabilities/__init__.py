"""Public capability catalog contracts."""

from sage_harness.capabilities.contracts import (
    CapabilityAvailability,
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityOrigin,
    CapabilityPermission,
    CapabilityRisk,
    CapabilitySurface,
)
from sage_harness.capabilities.registry import CapabilityRegistry
from sage_harness.capabilities.selection import (
    CapabilityBinding,
    CapabilityMatch,
    CapabilityRejection,
    CapabilitySelectionIndex,
    CapabilitySelectionOutcome,
    SelectionRejectionCode,
)

__all__ = [
    "CapabilityAvailability",
    "CapabilityBinding",
    "CapabilityDescriptor",
    "CapabilityKind",
    "CapabilityMatch",
    "CapabilityOrigin",
    "CapabilityPermission",
    "CapabilityRegistry",
    "CapabilityRejection",
    "CapabilityRisk",
    "CapabilitySelectionIndex",
    "CapabilitySelectionOutcome",
    "CapabilitySurface",
    "SelectionRejectionCode",
]
