"""Deterministic knowledge autonomy policy coverage."""

from core.knowledge.policy import KnowledgePolicyInput, evaluate_knowledge_policy


def test_local_private_source_is_the_only_auto_apply_path() -> None:
    result = evaluate_knowledge_policy(
        KnowledgePolicyInput(
            change_kind="ingest",
            source_kind="obsidian",
            target_path="wiki/sources/harness.md",
            visibility="private",
            parser_id="sage.markdown",
        )
    )

    assert result.risk_level == "low"
    assert result.action == "auto_apply"


def test_verified_extractive_learning_is_auto_applied_but_spoofing_is_blocked() -> None:
    verified = evaluate_knowledge_policy(
        KnowledgePolicyInput(
            change_kind="learning",
            source_kind="agent_learning",
            target_path="wiki/learnings/context.md",
            visibility="private",
            parser_id=None,
            evidence_verified=True,
            evidence_count=2,
            generator_id="sage.evidence-learning",
        )
    )
    spoofed = evaluate_knowledge_policy(
        KnowledgePolicyInput(
            change_kind="learning",
            source_kind="agent_learning",
            target_path="wiki/learnings/context.md",
            visibility="private",
            parser_id=None,
            evidence_verified=False,
            evidence_count=2,
            generator_id="model.freeform",
        )
    )

    assert verified.risk_level == "low"
    assert verified.action == "auto_apply"
    assert spoofed.action == "block"


def test_external_and_freeform_derived_content_remain_reviewable() -> None:
    external = evaluate_knowledge_policy(
        KnowledgePolicyInput(
            change_kind="ingest",
            source_kind="obsidian",
            target_path="wiki/sources/scanned.md",
            visibility="private",
            parser_id="qwen3-vl",
        )
    )
    synthesis = evaluate_knowledge_policy(
        KnowledgePolicyInput(
            change_kind="synthesis",
            source_kind="synthesis",
            target_path="overview.md",
            visibility="private",
            parser_id=None,
        )
    )

    assert external.action == "draft"
    assert external.risk_level == "medium"
    assert synthesis.action == "draft"

    spoofed = evaluate_knowledge_policy(
        KnowledgePolicyInput(
            change_kind="ingest",
            source_kind="obsidian",
            target_path="wiki/sources/spoofed.md",
            visibility="private",
            parser_id="sage.third-party",
        )
    )
    assert spoofed.action == "draft"


def test_destructive_and_unsafe_changes_never_auto_apply() -> None:
    rollback = evaluate_knowledge_policy(
        KnowledgePolicyInput(
            change_kind="rollback",
            source_kind="rollback",
            target_path="wiki/sources/harness.md",
            visibility="private",
            parser_id=None,
        )
    )
    public = evaluate_knowledge_policy(
        KnowledgePolicyInput(
            change_kind="ingest",
            source_kind="obsidian",
            target_path="wiki/sources/public.md",
            visibility="public",
            parser_id="sage.markdown",
        )
    )

    assert rollback.action == "require_review"
    assert rollback.risk_level == "high"
    assert public.action == "block"
    assert public.risk_level == "blocked"
