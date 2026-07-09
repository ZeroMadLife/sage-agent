"""Coding model output protocol tests."""

from core.coding.engine import parse


def test_parse_json_tool_payload() -> None:
    """The parser accepts JSON tool payloads inside <tool> tags."""
    kind, payload = parse('<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>')

    assert kind == "tool"
    assert payload == {"name": "read_file", "args": {"path": "README.md"}}


def test_parse_xml_tool_payload_with_nested_text() -> None:
    """The parser accepts XML-style tool attributes and nested patch fields."""
    raw = (
        '<tool name="patch_file" path="app.py">'
        "<old_text>return 1</old_text><new_text>return 2</new_text>"
        "</tool>"
    )

    kind, payload = parse(raw)

    assert kind == "tool"
    assert payload["name"] == "patch_file"
    assert payload["args"] == {
        "path": "app.py",
        "old_text": "return 1",
        "new_text": "return 2",
    }


def test_parse_final_payload() -> None:
    """The parser extracts final answers from <final> tags."""
    kind, payload = parse("<final>Done</final>")

    assert kind == "final"
    assert payload == "Done"


def test_parse_retry_notice_for_missing_protocol() -> None:
    """Plain text without protocol tags returns a retry notice."""
    kind, payload = parse("I will do it")

    assert kind == "retry"
    assert "missing <tool> or <final>" in payload
