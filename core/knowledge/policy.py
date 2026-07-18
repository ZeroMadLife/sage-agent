"""Deterministic autonomy policy for auditable knowledge proposals."""

from __future__ import annotations

from dataclasses import dataclass

POLICY_ID = "sage.knowledge-autonomy"
POLICY_VERSION = "1.1.0"
_TRUSTED_LOCAL_PARSERS = frozenset({"sage.markdown", "sage.html", "sage.pdf.text"})


def is_trusted_local_parser(parser_id: str) -> bool:
    return parser_id in _TRUSTED_LOCAL_PARSERS


@dataclass(frozen=True, slots=True)
class KnowledgePolicyInput:
    change_kind: str
    source_kind: str
    target_path: str
    visibility: str
    parser_id: str | None
    evidence_verified: bool = False
    evidence_count: int = 0
    generator_id: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgePolicyOutcome:
    risk_level: str
    action: str
    reason_codes: tuple[str, ...]


def evaluate_knowledge_policy(value: KnowledgePolicyInput) -> KnowledgePolicyOutcome:
    """Classify a proposal without consulting an LLM or mutable runtime state."""

    if value.visibility != "private":
        return KnowledgePolicyOutcome("blocked", "block", ("non_private_projection",))

    if value.change_kind == "ingest":
        if not value.target_path.startswith("wiki/sources/") or not value.target_path.endswith(
            ".md"
        ):
            return KnowledgePolicyOutcome("blocked", "block", ("target_outside_source_wiki",))
        if value.parser_id is None:
            return KnowledgePolicyOutcome("blocked", "block", ("missing_parse_evidence",))
        if value.source_kind == "web":
            return KnowledgePolicyOutcome(
                "medium",
                "draft",
                ("external_web_source", "human_review_required"),
            )
        if is_trusted_local_parser(value.parser_id):
            return KnowledgePolicyOutcome(
                "low",
                "auto_apply",
                ("private_source", "deterministic_local_parser", "bounded_wiki_target"),
            )
        return KnowledgePolicyOutcome(
            "medium",
            "draft",
            ("private_source", "external_parser_output", "human_review_required"),
        )

    if value.change_kind == "synthesis":
        if value.target_path != "overview.md":
            return KnowledgePolicyOutcome("blocked", "block", ("invalid_synthesis_target",))
        return KnowledgePolicyOutcome(
            "medium",
            "draft",
            ("derived_workspace_content", "human_review_required"),
        )

    if value.change_kind == "learning":
        if (
            value.source_kind != "agent_learning"
            or not value.target_path.startswith("wiki/learnings/")
            or not value.target_path.endswith(".md")
        ):
            return KnowledgePolicyOutcome(
                "blocked", "block", ("invalid_learning_target",)
            )
        if (
            not value.evidence_verified
            or value.generator_id != "sage.evidence-learning"
            or value.evidence_count < 1
            or value.evidence_count > 8
        ):
            return KnowledgePolicyOutcome(
                "blocked", "block", ("unverified_learning_evidence",)
            )
        return KnowledgePolicyOutcome(
            "low",
            "auto_apply",
            (
                "private_evidence_learning",
                "current_revision_citations",
                "extractive_content_only",
                "undo_available",
            ),
        )

    if value.change_kind in {"rollback", "retraction"}:
        return KnowledgePolicyOutcome(
            "high",
            "require_review",
            ("destructive_history_change", "explicit_user_action_required"),
        )

    return KnowledgePolicyOutcome("blocked", "block", ("unsupported_change_kind",))
