"""Evidence-only knowledge retrieval for the coding harness."""

from __future__ import annotations

import json
from typing import Any

from core.coding.context import WorkspaceContext
from core.coding.tools.base import ToolContext, ToolResult
from core.coding.tools.registry import register_tool
from core.coding.tools.schemas import KnowledgeLearnArgs, KnowledgeSearchArgs
from core.knowledge import KnowledgeStore


@register_tool(
    name="knowledge_search",
    description=(
        "Search approved Sage knowledge and return revision-bound citations. "
        "When status is no_evidence, do not claim the answer came from the knowledge base."
    ),
    schema={"query": "str", "top_k": "int=8", "token_budget": "int=3000"},
    schema_model=KnowledgeSearchArgs,
    risky=False,
    category="knowledge",
    requires_approval=False,
    timeout=30.0,
    deferred=False,
)
def knowledge_search(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Retrieve bounded evidence without exposing server filesystem paths."""

    _ = workspace
    store = tool_context.knowledge_store if tool_context is not None else None
    if not isinstance(store, KnowledgeStore):
        return ToolResult(
            content=json.dumps(
                {
                    "status": "unavailable",
                    "message": "knowledge workspace is not configured",
                    "citations": [],
                },
                ensure_ascii=False,
            ),
            is_error=True,
        )
    bundle = store.retrieve(
        str(args["query"]),
        top_k=int(args["top_k"]),
        token_budget=int(args["token_budget"]),
        relation_expand=store.knowledge_index.relevance_policy is not None,
    )
    payload = {
        "status": bundle.status,
        "query": bundle.query,
        "used_tokens": bundle.used_tokens,
        "token_budget": bundle.token_budget,
        "omitted_count": bundle.omitted_count,
        "recovery": {
            "status": bundle.recovery_status,
            "round_count": len(bundle.recovery_attempts),
            "no_evidence_reason": bundle.no_evidence_reason,
            "attempts": [
                {
                    "round_index": attempt.round_index,
                    "trigger_reason": attempt.trigger_reason,
                    "retrieval_mode": attempt.retrieval_mode,
                    "top_k": attempt.top_k,
                    "result_count": attempt.result_count,
                    "query_rewritten": attempt.query_rewritten,
                }
                for attempt in bundle.recovery_attempts
            ],
        },
        "instruction": (
            "Use only the cited excerpts for knowledge-base claims. After a useful "
            "investigation, ask the user whether these citation IDs should be persisted. "
            "Call knowledge_learn only after the user explicitly confirms; the deposit "
            "is extractive and reversible."
            if bundle.evidence
            else "The knowledge base has no evidence for this query. Say so explicitly."
        ),
        "citations": [
            {
                "citation_id": evidence.hit.citation_id,
                "rank": evidence.hit.rank,
                "page_revision": evidence.hit.chunk.page_revision,
                "source_revision": evidence.hit.chunk.source_revision,
                "source_kind": evidence.hit.chunk.source_kind,
                "source_relative_path": evidence.hit.chunk.source_relative_path,
                "title": evidence.hit.chunk.title,
                "heading_path": list(evidence.hit.chunk.heading_path),
                "block_id": evidence.hit.chunk.block_id,
                "page_number": evidence.hit.chunk.page_number,
                "block_kind": evidence.hit.chunk.block_kind,
                "bbox": evidence.hit.chunk.bbox,
                "bbox_coordinate_space": (
                    "normalized" if evidence.hit.chunk.bbox is not None else None
                ),
                "media_ref": evidence.hit.chunk.media_ref,
                "confidence": evidence.hit.chunk.confidence,
                "parser_id": evidence.hit.chunk.parser_id,
                "parser_version": evidence.hit.chunk.parser_version,
                "retrieval_route": evidence.hit.retrieval_route,
                "graph_edge_id": evidence.hit.graph_edge_id,
                "graph_evidence_citation_id": evidence.hit.graph_evidence_citation_id,
                "graph_seed_page_id": evidence.hit.graph_seed_page_id,
                "graph_direction": evidence.hit.graph_direction,
                "graph_score": evidence.hit.graph_score,
                "excerpt": evidence.excerpt,
                "truncated": evidence.truncated,
            }
            for evidence in bundle.evidence
        ],
    }
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))


@register_tool(
    name="knowledge_learn",
    description=(
        "Persist a reversible evidence snapshot from citation IDs returned by "
        "knowledge_search after explicit user confirmation. Freeform factual content "
        "is not accepted."
    ),
    schema={"topic": "str", "citation_ids": "list[str](1..8)"},
    schema_model=KnowledgeLearnArgs,
    risky=True,
    category="knowledge",
    requires_approval=True,
    timeout=30.0,
    deferred=False,
)
def knowledge_learn(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Deposit only server-resolved excerpts, never model-authored claims."""

    _ = workspace
    store = tool_context.knowledge_store if tool_context is not None else None
    runtime = tool_context.runtime if tool_context is not None else None
    if not isinstance(store, KnowledgeStore):
        return ToolResult(
            content=json.dumps(
                {"status": "unavailable", "message": "knowledge workspace is not configured"},
                ensure_ascii=False,
            ),
            is_error=True,
        )
    proposal = store.propose_evidence_learning(
        str(args["topic"]),
        tuple(str(item) for item in args["citation_ids"]),
        session_id=str(getattr(runtime, "session_id", "")),
        run_id=str(getattr(runtime, "active_run_id", "") or ""),
        event_id=str(getattr(runtime, "_turn_id", "") or ""),
    )
    decision = store.get_policy_decision(proposal.proposal_id)
    payload = {
        "status": "deposited",
        "proposal_id": proposal.proposal_id,
        "page_id": proposal.page_id,
        "target_path": proposal.target_path,
        "page_revision": decision.applied_page_revision if decision is not None else None,
        "citation_ids": list(args["citation_ids"]),
        "undo_available": bool(decision and decision.applied_page_revision),
    }
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))
