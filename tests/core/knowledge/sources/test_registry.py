from __future__ import annotations

from pathlib import Path

import pytest

from core.knowledge import KnowledgeSourceRoot
from core.knowledge.sources import (
    FilesystemKnowledgeSourceAdapter,
    KnowledgeSourceAdapterRegistry,
    KnowledgeSourceNotSupportedError,
)


def _source(kind: str) -> KnowledgeSourceRoot:
    return KnowledgeSourceRoot("source", kind, "Source", Path("."))


def test_registry_resolves_only_explicit_supported_kinds() -> None:
    adapter = FilesystemKnowledgeSourceAdapter()
    registry = KnowledgeSourceAdapterRegistry((adapter,))

    assert registry.resolve(_source("obsidian")) is adapter
    assert registry.resolve(_source("markdown"), adapter_id="filesystem") is adapter
    with pytest.raises(KnowledgeSourceNotSupportedError):
        registry.resolve(_source("github"))
    with pytest.raises(KnowledgeSourceNotSupportedError):
        registry.resolve(_source("obsidian"), adapter_id="github")


def test_registry_rejects_kind_ambiguity() -> None:
    registry = KnowledgeSourceAdapterRegistry((FilesystemKnowledgeSourceAdapter(),))

    with pytest.raises(ValueError, match="adapter id must be unique"):
        registry.register(FilesystemKnowledgeSourceAdapter())
