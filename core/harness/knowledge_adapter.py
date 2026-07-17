"""Read-only adapter from the reusable Harness port to Sage Knowledge."""

from __future__ import annotations

import asyncio
from typing import Any

from sage_harness import KnowledgeEvidence, KnowledgePort, KnowledgeRetrievalResult

from core.coding.runtime import CodingRuntime
from core.knowledge import KnowledgeStore

_UNAVAILABLE_WORKSPACE_ID = "knowledge-unavailable"


class CodingKnowledgePort(KnowledgePort):
    """Bind retrieval to the Knowledge workspace attached to one Coding runtime."""

    def __init__(self, runtime: CodingRuntime) -> None:
        self._store = runtime.tool_context.knowledge_store

    @property
    def available(self) -> bool:
        return isinstance(self._store, KnowledgeStore)

    @property
    def workspace_id(self) -> str:
        if not isinstance(self._store, KnowledgeStore):
            return _UNAVAILABLE_WORKSPACE_ID
        return str(self._store.knowledge_index.workspace_id)

    async def search(
        self,
        query: str,
        *,
        workspace_id: str,
        token_budget: int,
        top_k: int = 8,
    ) -> KnowledgeRetrievalResult:
        normalized = " ".join(query.split())
        if not normalized:
            raise ValueError("knowledge query must not be empty")
        if len(normalized) > 2_000:
            raise ValueError("knowledge query must not exceed 2000 characters")
        if top_k < 1 or top_k > 20:
            raise ValueError("knowledge top_k must be between 1 and 20")
        if token_budget < 256 or token_budget > 20_000:
            raise ValueError("knowledge token_budget must be between 256 and 20000")
        if workspace_id != self.workspace_id:
            raise PermissionError("knowledge workspace does not match adapter scope")
        if not isinstance(self._store, KnowledgeStore):
            return KnowledgeRetrievalResult(
                query=normalized,
                workspace_id=self.workspace_id,
                status="unavailable",
                token_budget=token_budget,
                used_tokens=0,
                omitted_count=0,
            )

        bundle = await asyncio.to_thread(
            self._store.retrieve,
            normalized,
            top_k=top_k,
            token_budget=token_budget,
        )
        evidence: list[KnowledgeEvidence] = []
        for item in bundle.evidence:
            hit = item.hit
            chunk = hit.chunk
            if chunk.workspace_id != self.workspace_id:
                raise PermissionError("knowledge evidence escaped adapter workspace")
            metadata: dict[str, Any] = {
                "rank": hit.rank,
                "title": chunk.title[:240],
                "heading_path": tuple(value[:160] for value in chunk.heading_path[:8]),
                "block_id": chunk.block_id[:160],
                "source_kind": chunk.source_kind[:80],
                "source_relative_path": chunk.source_relative_path[:500],
                "token_count": item.token_count,
                "truncated": item.truncated,
            }
            evidence.append(
                KnowledgeEvidence(
                    citation_id=hit.citation_id,
                    content=item.excerpt,
                    page_revision=chunk.page_revision,
                    source_revision=chunk.source_revision,
                    score=hit.rrf_score,
                    metadata=metadata,
                )
            )
        return KnowledgeRetrievalResult(
            query=bundle.query,
            workspace_id=self.workspace_id,
            status=bundle.status,
            token_budget=bundle.token_budget,
            used_tokens=bundle.used_tokens,
            omitted_count=bundle.omitted_count,
            evidence=tuple(evidence),
        )


__all__ = ["CodingKnowledgePort"]
