"""Durable, parent-scoped evidence bundle adapter tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.coding.runtime import CodingRuntime
from core.harness.evidence_bundle import CodingEvidenceBundlePort


class FakeModel:
    async def complete(self, prompt: str) -> str:
        _ = prompt
        return "<final>unused</final>"


def _runtime(tmp_path: Path) -> CodingRuntime:
    model = FakeModel()
    runtime = CodingRuntime(
        session_id="session-parent",
        workspace_root=tmp_path,
        model=model,
        model_factory=lambda: model,
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    runtime.active_run_id = "run-parent"
    return runtime


def _record_child(
    runtime: CodingRuntime,
    child_run_id: str,
    *,
    tool: str,
    payload: dict[str, object],
    evidence_refs: list[str],
    parent_run_id: str = "run-parent",
) -> None:
    runtime.run_store.start_run(child_run_id)
    runtime.run_store.append_trace(
        child_run_id,
        {
            "type": "subagent_started",
            "run_id": child_run_id,
            "parent_run_id": parent_run_id,
            "subagent_type": "research",
            "status": "running",
        },
    )
    runtime.run_store.append_trace(
        child_run_id,
        {
            "type": "tool_result",
            "run_id": child_run_id,
            "parent_run_id": parent_run_id,
            "tool": tool,
            "content": json.dumps(payload),
            "is_error": False,
        },
    )
    runtime.run_store.append_trace(
        child_run_id,
        {
            "type": "subagent_terminal",
            "run_id": child_run_id,
            "parent_run_id": parent_run_id,
            "status": "succeeded",
            "evidence_refs": evidence_refs,
        },
    )


def test_evidence_bundle_reads_only_successful_parent_scoped_receipts(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _record_child(
        runtime,
        "child-knowledge",
        tool="knowledge_search",
        payload={
            "status": "evidence_found",
            "citations": [
                {
                    "citation_id": "kcite_one",
                    "title": "Approved note",
                    "excerpt": "Durable Knowledge evidence.",
                    "page_revision": "page-r1",
                    "source_revision": "source-r1",
                    "source_relative_path": "notes/harness.md",
                    "source_kind": "obsidian",
                    "block_id": "block-1",
                }
            ],
        },
        evidence_refs=["kcite_one"],
    )
    _record_child(
        runtime,
        "child-other-parent",
        tool="search_web",
        payload={
            "status": "evidence_found",
            "citations": [
                {
                    "citation_id": "wcite_other",
                    "url": "https://example.com/other",
                    "title": "Other run",
                    "excerpt": "Must not escape its parent run.",
                }
            ],
        },
        evidence_refs=["wcite_other"],
        parent_run_id="run-other",
    )

    bundle = asyncio.run(
        CodingEvidenceBundlePort(runtime).read(
            "session-parent",
            "run-parent",
            child_run_ids=("child-knowledge", "child-other-parent"),
            evidence_refs=("kcite_one", "wcite_other", "wcite_missing"),
            token_budget=1_000,
        )
    )

    assert bundle.status == "evidence_found"
    assert [item.evidence_ref for item in bundle.items] == ["kcite_one"]
    assert bundle.items[0].page_revision == "page-r1"
    assert bundle.items[0].metadata["block_id"] == "block-1"
    assert bundle.missing_refs == ("wcite_other", "wcite_missing")


def test_evidence_bundle_prefers_fetched_content_and_deduplicates_one_source(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    url = "https://example.com/guide"
    _record_child(
        runtime,
        "child-search",
        tool="search_web",
        payload={
            "status": "evidence_found",
            "provider": "fake",
            "citations": [
                {
                    "citation_id": "wcite_search",
                    "url": url,
                    "title": "Search result",
                    "excerpt": "Short search excerpt.",
                    "content_hash": "a" * 64,
                }
            ],
        },
        evidence_refs=["wcite_search"],
    )
    _record_child(
        runtime,
        "child-fetch",
        tool="fetch_web",
        payload={
            "status": "evidence_found",
            "citation_id": "wcite_fetch",
            "artifact_ref": "sage://coding/session-parent/child-fetch/fetch",
            "url": url,
            "title": "Fetched result",
            "excerpt": "Longer fetched evidence.",
            "content_hash": "b" * 64,
            "original_chars": 400,
        },
        evidence_refs=[
            "wcite_fetch",
            "sage://coding/session-parent/child-fetch/fetch",
        ],
    )

    bundle = asyncio.run(
        CodingEvidenceBundlePort(runtime).read(
            "session-parent",
            "run-parent",
            child_run_ids=("child-search", "child-fetch"),
            evidence_refs=(
                "wcite_search",
                "wcite_fetch",
                "sage://coding/session-parent/child-fetch/fetch",
            ),
            token_budget=1_000,
        )
    )

    assert len(bundle.items) == 1
    assert bundle.items[0].kind == "web_fetch"
    assert bundle.items[0].evidence_ref == "wcite_fetch"
    assert bundle.duplicate_count == 1
    assert bundle.missing_refs == ()


def test_evidence_bundle_clips_first_item_to_budget(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _record_child(
        runtime,
        "child-long",
        tool="search_web",
        payload={
            "status": "evidence_found",
            "citations": [
                {
                    "citation_id": "wcite_long",
                    "url": "https://example.com/long",
                    "title": "Long evidence",
                    "excerpt": "evidence " * 2_000,
                }
            ],
        },
        evidence_refs=["wcite_long"],
    )

    bundle = asyncio.run(
        CodingEvidenceBundlePort(runtime).read(
            "session-parent",
            "run-parent",
            child_run_ids=("child-long",),
            evidence_refs=("wcite_long",),
            token_budget=256,
        )
    )

    assert len(bundle.items) == 1
    assert bundle.items[0].truncated is True
    assert bundle.used_tokens <= 256
