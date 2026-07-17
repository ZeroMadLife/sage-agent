"""Safe and deterministic HTML parser coverage."""

from core.knowledge.parsing import HtmlParser, ParseRequest


def test_html_parser_removes_active_content_and_preserves_semantic_blocks() -> None:
    request = ParseRequest(
        source_id="src_html",
        relative_path="docs/guide.html",
        source_revision="sha256:html",
        media_type="text/html",
        payload=(
            b"<!doctype html><html><head><title>Sage Guide</title>"
            b"<style>.secret{display:none}</style><script>alert('x')</script></head>"
            b"<body><h1>Agent Harness</h1><p>Durable execution.</p>"
            b"<ul><li>Lease</li><li>Retry</li></ul>"
            b"<pre><code>sage run</code></pre><table><tr><td>Recall</td>"
            b"<td>0.91</td></tr></table></body></html>"
        ),
    )

    document = HtmlParser().parse(request)

    assert document.title == "Sage Guide"
    assert document.provenance.parser_id == "sage.html"
    assert [block.kind for block in document.blocks] == [
        "heading",
        "paragraph",
        "list",
        "code",
        "table",
    ]
    assert document.blocks[1].heading_path == ("Agent Harness",)
    assert "alert" not in document.rendered_markdown
    assert "display:none" not in document.rendered_markdown
    assert "Durable execution." in document.rendered_markdown
    assert "- Lease" in document.rendered_markdown
    assert "| Recall | 0.91 |" in document.rendered_markdown
    assert HtmlParser().parse(request) == document
