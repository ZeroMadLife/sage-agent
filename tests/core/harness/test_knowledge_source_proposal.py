"""Harness proposal-only web source tool and timeline projection."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import ToolMessage
from sage_harness import KnowledgeSourceProposalReceipt
from sage_harness.runtime.events import HarnessStreamItem

from core.coding.runtime import CodingRuntime
from core.harness.event_adapter import HarnessEventAdapter
from core.harness.tools_adapter import build_deerflow_coding_tool_bundle


@dataclass
class FakeProposalPort:
    available: bool = True

    async def propose(
        self,
        thread_id: str,
        run_id: str,
        artifact_ref: str,
        *,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> KnowledgeSourceProposalReceipt:
        assert thread_id == "proposal-thread"
        assert run_id == "proposal-run"
        assert artifact_ref.startswith("sage://coding/")
        assert reason == "Keep the official source"
        assert evidence_refs == ("wcite_123",)
        return KnowledgeSourceProposalReceipt(
            proposal_id="ksprop_123",
            thread_id=thread_id,
            run_id=run_id,
            status="pending",
            revision=1,
            source_kind="web",
            title="Private remote title",
            content_hash="a" * 64,
        )


def test_save_web_source_is_deferred_and_returns_only_review_receipt(tmp_path: Path) -> None:
    runtime = CodingRuntime(
        session_id="proposal-thread",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    bundle = build_deerflow_coding_tool_bundle(
        runtime,
        run_id="proposal-run",
        knowledge_source_proposal_port=FakeProposalPort(),
    )
    tool = next(item for item in bundle.tools if item.name == "save_web_source")

    content = asyncio.run(
        tool.ainvoke(
            {
                "artifact_ref": (
                    "sage://coding/proposal-thread/runs/proposal-run/"
                    "tool-results/call-fetch.txt"
                ),
                "reason": "Keep the official source",
                "evidence_refs": ["wcite_123"],
            }
        )
    )
    payload = json.loads(content)

    assert "save_web_source" in bundle.deferred_setup.deferred_names
    assert payload == {
        "proposal_id": "ksprop_123",
        "proposal_type": "knowledge_source",
        "status": "pending",
        "revision": 1,
        "source_kind": "web",
        "content_hash": "a" * 64,
        "requires_user_confirmation": True,
        "instruction": (
            "The source is not in durable Knowledge yet. The user must approve "
            "this proposal through the review API."
        ),
    }
    assert bundle.deferred_setup.selection_index is not None
    selected = bundle.deferred_setup.selection_index.select(("web:save-source",))
    assert len(selected.selected) == 1
    assert selected.selected[0].descriptor.remote_content is False


def test_source_receipt_projects_one_safe_proposal_event() -> None:
    receipt = json.dumps(
        {
            "proposal_id": "ksprop_123",
            "status": "pending",
            "revision": 1,
            "source_kind": "web",
            "content_hash": "a" * 64,
            "canonical_url": "https://must-not-leak.example/private",
        }
    )
    events = HarnessEventAdapter(
        session_id="proposal-thread",
        run_id="proposal-run",
    ).adapt(
        HarnessStreamItem(
            1,
            "messages",
            (
                ToolMessage(
                    content=receipt,
                    tool_call_id="call-save",
                    name="save_web_source",
                ),
                {},
            ),
            "proposal-source-event",
        )
    )

    assert [event.kind for event in events] == ["tool", "proposal"]
    proposal = events[-1]
    assert proposal.status == "pending"
    assert proposal.payload["proposal_id"] == "ksprop_123"
    assert proposal.payload["requires_user_confirmation"] is True
    assert "canonical_url" not in proposal.payload
    assert "must-not-leak" not in str(proposal.payload)
