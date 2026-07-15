"""Deterministic Parser Registry and Markdown parser contracts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.knowledge.parsing import (
    MarkdownParser,
    ParserConflictError,
    ParseRequest,
    ParserNotFoundError,
    ParserRegistry,
    default_parser_registry,
)


def _request(path: str = "notes/harness.md") -> ParseRequest:
    return ParseRequest(
        source_id="src_123",
        relative_path=path,
        source_revision="sha256:source",
        media_type="text/markdown",
        payload=(
            "---\nvisibility: private\n---\n\n"
            "# Agent Harness\n\n可恢复执行。\n\n"
            "## Runtime\n\n- lease\n- retry\n\n"
            "```python\nprint('sage')\n```\n"
        ).encode(),
    )


def test_markdown_parser_produces_stable_heading_aware_blocks() -> None:
    parser = MarkdownParser()

    first = parser.parse(_request())
    repeated = parser.parse(_request())

    assert repeated == first
    assert first.title == "Agent Harness"
    assert first.provenance.parser_id == "sage.markdown"
    assert first.provenance.parser_version == "1.0.0"
    assert first.provenance.input_revision == "sha256:source"
    assert first.document_id.startswith("pdoc_")
    assert [block.kind for block in first.blocks] == [
        "frontmatter",
        "heading",
        "paragraph",
        "heading",
        "list",
        "code",
    ]
    assert first.blocks[2].heading_path == ("Agent Harness",)
    assert first.blocks[-1].heading_path == ("Agent Harness", "Runtime")
    assert len({block.block_id for block in first.blocks}) == len(first.blocks)
    assert all(block.confidence == 1.0 for block in first.blocks)
    assert first.rendered_markdown == _request().payload.decode()


def test_registry_routes_deterministically_and_rejects_ambiguous_registration() -> None:
    registry = ParserRegistry()
    parser = MarkdownParser()
    registry.register(parser)

    assert registry.resolve(_request()).parser_id == "sage.markdown"
    assert registry.parse(_request()) == parser.parse(_request())
    with pytest.raises(ParserConflictError, match="already registered"):
        registry.register(MarkdownParser())
    with pytest.raises(ParserNotFoundError, match="no parser"):
        registry.parse(
            replace(
                _request("notes/data.bin"),
                media_type="application/octet-stream",
            )
        )


def test_default_registry_is_fresh_and_contains_markdown() -> None:
    first = default_parser_registry()
    second = default_parser_registry()

    assert first is not second
    assert first.resolve(_request()).parser_id == "sage.markdown"
    assert first.parser_ids() == ("sage.html", "sage.markdown", "sage.pdf.text")
