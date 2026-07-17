"""Fail-closed registry for trusted knowledge source adapters."""

from __future__ import annotations

from collections.abc import Iterable

from core.knowledge.store import KnowledgeSourceRoot

from .errors import KnowledgeSourceNotSupportedError
from .types import KnowledgeSourceAdapter


class KnowledgeSourceAdapterRegistry:
    """Resolve one explicit adapter for each configured source kind."""

    def __init__(self, adapters: Iterable[KnowledgeSourceAdapter] = ()) -> None:
        self._by_kind: dict[str, KnowledgeSourceAdapter] = {}
        self._by_id: dict[str, KnowledgeSourceAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: KnowledgeSourceAdapter) -> None:
        adapter_id = adapter.adapter_id.strip()
        if not adapter_id or adapter_id in self._by_id:
            raise ValueError("knowledge source adapter id must be unique")
        kinds = {kind.strip().lower() for kind in adapter.supported_kinds if kind.strip()}
        if not kinds:
            raise ValueError("knowledge source adapter must support at least one kind")
        conflicts = sorted(kind for kind in kinds if kind in self._by_kind)
        if conflicts:
            raise ValueError(f"knowledge source kinds already registered: {', '.join(conflicts)}")
        self._by_id[adapter_id] = adapter
        for kind in kinds:
            self._by_kind[kind] = adapter

    def resolve(
        self,
        source: KnowledgeSourceRoot,
        *,
        adapter_id: str | None = None,
    ) -> KnowledgeSourceAdapter:
        adapter = self._by_kind.get(source.kind.strip().lower())
        if adapter is None or (adapter_id is not None and adapter.adapter_id != adapter_id):
            raise KnowledgeSourceNotSupportedError(
                "knowledge source connector is not available"
            )
        return adapter

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_id))
