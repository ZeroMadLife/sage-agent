"""Read-only evidence bundles reconstructed from durable Research child traces."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace

from sage_harness import EvidenceBundle, EvidenceBundleItem, EvidenceBundlePort

from core.coding.runtime import CodingRuntime
from core.harness.web_evidence import estimated_tokens, fit_excerpt

_MIN_TOKEN_BUDGET = 256
_MAX_TOKEN_BUDGET = 20_000
_MAX_CHILD_RUNS = 6
_MAX_EVIDENCE_REFS = 128
_EVIDENCE_TOOLS = frozenset({"knowledge_search", "search_web", "fetch_web"})
_KIND_PRIORITY = {"web_fetch": 0, "knowledge": 1, "web_search": 2}


class CodingEvidenceBundlePort(EvidenceBundlePort):
    """Resolve only evidence already authorized by successful child receipts."""

    def __init__(self, runtime: CodingRuntime) -> None:
        self._runtime = runtime

    @property
    def available(self) -> bool:
        return True

    async def read(
        self,
        thread_id: str,
        parent_run_id: str,
        *,
        child_run_ids: Sequence[str],
        evidence_refs: Sequence[str],
        token_budget: int,
    ) -> EvidenceBundle:
        if thread_id != self._runtime.session_id:
            raise PermissionError("evidence thread does not match adapter scope")
        if parent_run_id != self._runtime.active_run_id:
            raise PermissionError("evidence parent run is not active")
        if not _MIN_TOKEN_BUDGET <= token_budget <= _MAX_TOKEN_BUDGET:
            raise ValueError("evidence token_budget must be between 256 and 20000")
        child_ids = _bounded_unique(child_run_ids, _MAX_CHILD_RUNS)
        requested = _bounded_unique(evidence_refs, _MAX_EVIDENCE_REFS)
        if not child_ids or not requested:
            return EvidenceBundle(
                status="no_evidence",
                requested_refs=requested,
                missing_refs=requested,
                token_budget=token_budget,
            )
        return await asyncio.to_thread(
            self._read_sync,
            parent_run_id,
            child_ids,
            requested,
            token_budget,
        )

    def _read_sync(
        self,
        parent_run_id: str,
        child_run_ids: tuple[str, ...],
        requested: tuple[str, ...],
        token_budget: int,
    ) -> EvidenceBundle:
        requested_set = set(requested)
        candidates: list[EvidenceBundleItem] = []
        resolved_aliases: set[str] = set()
        for child_run_id in child_run_ids:
            try:
                events = self._runtime.run_store.get_run(child_run_id)["events"]
            except FileNotFoundError:
                continue
            authorized = _authorized_refs(events, parent_run_id) & requested_set
            if not authorized:
                continue
            for event in events:
                if (
                    event.get("type") != "tool_result"
                    or event.get("is_error") is True
                    or str(event.get("tool", "")) not in _EVIDENCE_TOOLS
                ):
                    continue
                items, aliases = _items_from_tool_result(event, authorized)
                candidates.extend(items)
                resolved_aliases.update(aliases)

        deduplicated, duplicate_count = _deduplicate_sources(candidates)
        selected, used_tokens, budget_omitted = _fit_items(deduplicated, token_budget)
        missing = tuple(ref for ref in requested if ref not in resolved_aliases)
        omitted_count = budget_omitted + len(missing)
        return EvidenceBundle(
            status="evidence_found" if selected else "no_evidence",
            items=selected,
            requested_refs=requested,
            missing_refs=missing,
            duplicate_count=duplicate_count,
            token_budget=token_budget,
            used_tokens=used_tokens,
            omitted_count=omitted_count,
        )


def _authorized_refs(events: Sequence[Mapping[str, object]], parent_run_id: str) -> set[str]:
    started = any(
        event.get("type") == "subagent_started"
        and event.get("parent_run_id") == parent_run_id
        and event.get("subagent_type") == "research"
        for event in events
    )
    if not started:
        return set()
    for event in reversed(events):
        if event.get("type") != "subagent_terminal":
            continue
        if event.get("parent_run_id") != parent_run_id or event.get("status") != "succeeded":
            return set()
        refs = event.get("evidence_refs")
        return set(_bounded_unique(refs if isinstance(refs, list | tuple) else (), 128))
    return set()


def _items_from_tool_result(
    event: Mapping[str, object],
    authorized: set[str],
) -> tuple[list[EvidenceBundleItem], set[str]]:
    try:
        payload = json.loads(str(event.get("content", "")))
    except (TypeError, ValueError):
        return [], set()
    if not isinstance(payload, Mapping) or payload.get("status") != "evidence_found":
        return [], set()
    tool = str(event.get("tool", ""))
    if tool == "knowledge_search":
        return _knowledge_items(payload, authorized)
    if tool == "search_web":
        return _web_search_items(payload, authorized)
    if tool == "fetch_web":
        return _web_fetch_items(payload, authorized)
    return [], set()


def _knowledge_items(
    payload: Mapping[str, object],
    authorized: set[str],
) -> tuple[list[EvidenceBundleItem], set[str]]:
    items: list[EvidenceBundleItem] = []
    aliases: set[str] = set()
    citations = payload.get("citations")
    for citation in citations if isinstance(citations, list) else ():
        if not isinstance(citation, Mapping):
            continue
        ref = str(citation.get("citation_id", "")).strip()
        if ref not in authorized:
            continue
        source_revision = str(citation.get("source_revision", ""))[:160]
        source_path = str(citation.get("source_relative_path", ""))[:500]
        page_revision = str(citation.get("page_revision", ""))[:160]
        source_ref = _source_ref("knowledge", source_revision, source_path, page_revision)
        content = str(citation.get("excerpt", "")).strip()
        items.append(
            EvidenceBundleItem(
                evidence_ref=ref,
                kind="knowledge",
                content=content,
                title=str(citation.get("title", ""))[:300],
                source_ref=source_ref,
                page_revision=page_revision,
                source_revision=source_revision,
                content_hash=hashlib.sha256(content.encode("utf-8", "replace")).hexdigest(),
                token_count=estimated_tokens(content),
                truncated=bool(citation.get("truncated")),
                metadata={
                    "block_id": str(citation.get("block_id", ""))[:160],
                    "source_kind": str(citation.get("source_kind", ""))[:80],
                },
            )
        )
        aliases.add(ref)
    return items, aliases


def _web_search_items(
    payload: Mapping[str, object],
    authorized: set[str],
) -> tuple[list[EvidenceBundleItem], set[str]]:
    items: list[EvidenceBundleItem] = []
    aliases: set[str] = set()
    citations = payload.get("citations")
    for citation in citations if isinstance(citations, list) else ():
        if not isinstance(citation, Mapping):
            continue
        ref = str(citation.get("citation_id", "")).strip()
        if ref not in authorized:
            continue
        url = str(citation.get("url", ""))[:2_000]
        content = str(citation.get("excerpt", "")).strip()
        items.append(
            EvidenceBundleItem(
                evidence_ref=ref,
                kind="web_search",
                content=content,
                title=str(citation.get("title", ""))[:300],
                source_ref=_source_ref("web", url),
                canonical_url=url,
                content_hash=str(citation.get("content_hash", ""))[:128],
                token_count=estimated_tokens(url, content),
                metadata={"provider": str(payload.get("provider", ""))[:80]},
            )
        )
        aliases.add(ref)
    return items, aliases


def _web_fetch_items(
    payload: Mapping[str, object],
    authorized: set[str],
) -> tuple[list[EvidenceBundleItem], set[str]]:
    citation_ref = str(payload.get("citation_id", "")).strip()
    artifact_ref = str(payload.get("artifact_ref", "")).strip()
    aliases = {ref for ref in (citation_ref, artifact_ref) if ref in authorized}
    if not aliases:
        return [], set()
    evidence_ref = citation_ref if citation_ref in aliases else artifact_ref
    url = str(payload.get("url", ""))[:2_000]
    content = str(payload.get("excerpt", "")).strip()
    return [
        EvidenceBundleItem(
            evidence_ref=evidence_ref,
            kind="web_fetch",
            content=content,
            title=str(payload.get("title", ""))[:300],
            source_ref=_source_ref("web", url),
            canonical_url=url,
            content_hash=str(payload.get("content_hash", ""))[:128],
            token_count=estimated_tokens(url, content),
            truncated=bool(payload.get("original_chars", 0)) and content.endswith("..."),
            metadata={"artifact_ref": artifact_ref[:1_000]},
        )
    ], aliases


def _deduplicate_sources(
    items: Sequence[EvidenceBundleItem],
) -> tuple[tuple[EvidenceBundleItem, ...], int]:
    ordered = sorted(
        items,
        key=lambda item: (_KIND_PRIORITY[item.kind], item.source_ref, item.evidence_ref),
    )
    selected: list[EvidenceBundleItem] = []
    seen_refs: set[str] = set()
    seen_sources: set[str] = set()
    for item in ordered:
        if item.evidence_ref in seen_refs or (item.source_ref and item.source_ref in seen_sources):
            continue
        seen_refs.add(item.evidence_ref)
        if item.source_ref:
            seen_sources.add(item.source_ref)
        selected.append(item)
    return tuple(selected), max(0, len(items) - len(selected))


def _fit_items(
    items: Sequence[EvidenceBundleItem],
    token_budget: int,
) -> tuple[tuple[EvidenceBundleItem, ...], int, int]:
    selected: list[EvidenceBundleItem] = []
    used_tokens = 0
    for item in items:
        remaining = token_budget - used_tokens
        if remaining <= 0:
            break
        overhead = (item.title, item.canonical_url, item.evidence_ref)
        tokens = estimated_tokens(*overhead, item.content)
        if tokens <= remaining:
            selected.append(replace(item, token_count=tokens))
            used_tokens += tokens
            continue
        if selected:
            break
        content = fit_excerpt(item.content, token_budget=remaining, overhead=overhead)
        if not content:
            break
        tokens = estimated_tokens(*overhead, content)
        selected.append(replace(item, content=content, token_count=tokens, truncated=True))
        used_tokens += tokens
        break
    return tuple(selected), used_tokens, max(0, len(items) - len(selected))


def _source_ref(*parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8", "replace")).hexdigest()[:24]
    return f"source_{digest}"


def _bounded_unique(values: Sequence[str], limit: int) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item).strip() for item in values if isinstance(item, str) and item.strip()
        )
    )[:limit]


__all__ = ["CodingEvidenceBundlePort"]
