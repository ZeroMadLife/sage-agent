"""Approved-only workspace synthesis and citation coverage."""

from core.knowledge.synthesis import (
    deserialize_synthesis,
    serialize_synthesis,
    source_evidence,
    synthesize_workspace,
)
from core.knowledge.understanding import SourceUnderstanding


def _understanding() -> SourceUnderstanding:
    from core.knowledge.understanding import SourceSection, UnderstandingCitation

    return SourceUnderstanding(
        understanding_id="kund_source",
        artifact_id="part_source",
        source_id="src_source",
        source_revision="sha256:source",
        title="Agent Harness",
        summary="可恢复、可审核的执行系统。",
        sections=(SourceSection("Harness", ("pblk_1",)),),
        topics=("agent", "harness"),
        block_kind_counts=(("paragraph", 1),),
        citations=(UnderstandingCitation("pblk_1", None, ("Harness",)),),
        generator_id="sage.extractive",
        generator_version="1.0.0",
    )


def test_workspace_synthesis_is_deterministic_and_revision_cited() -> None:
    evidence = source_evidence(
        page_id="page_source",
        page_revision="krev_1",
        proposal_id="kprop_1",
        path="wiki/sources/harness.md",
        understanding=_understanding(),
    )

    first = synthesize_workspace((evidence,))
    second = synthesize_workspace((evidence,))

    assert first == second
    assert first.synthesis_id.startswith("ksyn_")
    assert first.input_hash.startswith("sha256:")
    assert "krev_1" in first.rendered_markdown
    assert "pblk_1" in first.rendered_markdown
    assert deserialize_synthesis(serialize_synthesis(first)) == first
