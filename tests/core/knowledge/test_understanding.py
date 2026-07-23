"""Deterministic Source Understanding coverage."""

from core.knowledge.parsing import MarkdownParser, ParseRequest
from core.knowledge.understanding import (
    deserialize_understanding,
    serialize_understanding,
    understand_source,
)


def test_understanding_is_deterministic_bounded_and_cited() -> None:
    request = ParseRequest(
        source_id="src_understanding",
        relative_path="harness.md",
        source_revision="sha256:understanding",
        media_type="text/markdown",
        payload=(
            "# Agent Harness\n\n可恢复执行与工具隔离。\n\n" "## Memory\n\n长期记忆和上下文压缩。\n"
        ).encode(),
    )
    document = MarkdownParser().parse(request)

    first = understand_source("part_understanding", document)
    second = understand_source("part_understanding", document)

    assert first == second
    assert first.understanding_id.startswith("kund_")
    assert "可恢复执行" in first.summary
    assert {section.title for section in first.sections} == {"Agent Harness", "Memory"}
    assert {citation.block_id for citation in first.citations}.issubset(
        {block.block_id for block in document.blocks}
    )
    assert "memory" in first.topics
    assert first.block_kind_counts
    assert deserialize_understanding(serialize_understanding(first)) == first
