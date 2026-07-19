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
from sage_harness.capabilities.telemetry import (
    CapabilityFailureCategory,
    CapabilityTelemetryMiddleware,
)

__all__ = [
    "CapabilityAvailability",
    "CapabilityBinding",
    "CapabilityDescriptor",
    "CapabilityFailureCategory",
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
    "CapabilityTelemetryMiddleware",
    "SelectionRejectionCode",
]
