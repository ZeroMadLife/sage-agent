"""Knowledge source connector foundation."""

from .errors import (
    KnowledgeScanError,
    KnowledgeSourceCheckpointConflictError,
    KnowledgeSourceError,
    KnowledgeSourceNotSupportedError,
)
from .filesystem import (
    FilesystemKnowledgeSourceAdapter,
    descriptor_checkpoint,
    fetch_filesystem_artifact,
    read_filesystem_revision,
    scan_filesystem_descriptors,
)
from .ingest import fetch_source_by_key
from .registry import KnowledgeSourceAdapterRegistry
from .types import (
    ImmutableSourceArtifact,
    KnowledgeSourceAdapter,
    SourceDescriptor,
    SourceScanPage,
)


def default_source_adapter_registry() -> KnowledgeSourceAdapterRegistry:
    return KnowledgeSourceAdapterRegistry((FilesystemKnowledgeSourceAdapter(),))


__all__ = [
    "FilesystemKnowledgeSourceAdapter",
    "ImmutableSourceArtifact",
    "KnowledgeScanError",
    "KnowledgeSourceAdapter",
    "KnowledgeSourceAdapterRegistry",
    "KnowledgeSourceCheckpointConflictError",
    "KnowledgeSourceError",
    "KnowledgeSourceNotSupportedError",
    "SourceDescriptor",
    "SourceScanPage",
    "default_source_adapter_registry",
    "descriptor_checkpoint",
    "fetch_filesystem_artifact",
    "fetch_source_by_key",
    "read_filesystem_revision",
    "scan_filesystem_descriptors",
]
