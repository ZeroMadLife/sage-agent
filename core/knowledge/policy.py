"""Deterministic autonomy policy for auditable knowledge proposals."""

from __future__ import annotations

from dataclasses import dataclass

POLICY_ID = "sage.knowledge-autonomy"
POLICY_VERSION = "1.0.0"
_TRUSTED_LOCAL_PARSERS = frozenset({"sage.markdown", "sage.html", "sage.pdf.text"})


@dataclass(frozen=True, slots=True)
class KnowledgePolicyInput:
    change_kind: str
    source_kind: str
    target_path: str
    visibility: str
    parser_id: str | None


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
        if value.parser_id in _TRUSTED_LOCAL_PARSERS:
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

    if value.change_kind in {"rollback", "retraction"}:
        return KnowledgePolicyOutcome(
            "high",
            "require_review",
            ("destructive_history_change", "explicit_user_action_required"),
        )

    return KnowledgePolicyOutcome("blocked", "block", ("unsupported_change_kind",))
