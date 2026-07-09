"""Parser for the coding agent model output protocol."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

ParseKind = Literal["tool", "tools", "final", "retry"]
ParseResult = tuple[ParseKind, Any]


def parse(raw: str) -> ParseResult:
    """Parse model output into a tool request, final answer, or retry notice."""
    text = str(raw)
    if "<tool" in text and ("<final>" not in text or text.find("<tool") < text.find("<final>")):
        parsed = parse_tool_blocks(text)
        if isinstance(parsed, str):
            return "retry", retry_notice(parsed)
        if parsed:
            if len(parsed) == 1:
                return "tool", parsed[0]
            return "tools", parsed

    if "<final>" in text:
        return "final", extract(text, "final")

    if not text.strip():
        return "retry", retry_notice("empty response")
    return "retry", retry_notice("missing <tool> or <final> tag")


def retry_notice(problem: str | None = None) -> str:
    """Return a compact correction instruction for malformed model output."""
    detail = f" Problem: {problem}." if problem else ""
    return (
        "Your previous response could not be executed."
        f"{detail} Return one or more valid <tool> calls, or one <final> answer."
    )


def parse_tool_blocks(raw: str) -> list[dict[str, Any]] | str:
    """Parse one or more <tool> blocks."""
    tools: list[dict[str, Any]] = []
    errors: list[str] = []
    pattern = r"<tool\b(?P<attrs>[^>]*)>(?P<body>.*?)</tool>"
    for match in re.finditer(pattern, raw, flags=re.DOTALL):
        attrs = parse_attrs(match.group("attrs"))
        if attrs.get("name", "").strip():
            parsed_xml = parse_xml_tool_match(match)
            if parsed_xml:
                tools.append(parsed_xml)
            continue

        body = match.group("body").strip()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            errors.append("tool payload must be valid JSON or supported XML")
            continue
        normalized = normalize_tool_payload(payload)
        if isinstance(normalized, str):
            errors.append(normalized)
            continue
        tools.extend(normalized)
    if tools:
        return tools
    if errors:
        return errors[0]
    return []


def normalize_tool_payload(payload: Any) -> list[dict[str, Any]] | str:
    """Normalize JSON payloads to a list of tool call dictionaries."""
    if isinstance(payload, list):
        if not payload:
            return "tool JSON list must not be empty"
        normalized: list[dict[str, Any]] = []
        for item in payload:
            parsed = normalize_tool_payload(item)
            if isinstance(parsed, str):
                return parsed
            normalized.extend(parsed)
        return normalized

    if not isinstance(payload, dict) or "name" not in payload:
        return "tool JSON must be an object with name and args"
    args = payload.get("args", {})
    if not isinstance(args, dict):
        return "tool args must be an object"
    return [{"name": str(payload["name"]), "args": args}]


def parse_xml_tool_match(match: re.Match[str]) -> dict[str, Any] | None:
    """Parse XML-style tool tags with attributes and nested text fields."""
    attrs = parse_attrs(match.group("attrs"))
    body = match.group("body")
    name = attrs.get("name", "").strip()
    if not name:
        return None
    args = {key: value for key, value in attrs.items() if key != "name"}
    for tag in ("content", "old_text", "new_text"):
        value = extract_raw(body, tag)
        if value is not None:
            args[tag] = value
    if name == "write_file" and "content" not in args and body.strip():
        args["content"] = body
    return {"name": name, "args": args}


def parse_attrs(text: str) -> dict[str, str]:
    """Parse simple quoted XML attributes."""
    return {
        key: value
        for key, value in re.findall(
            r'([A-Za-z_][A-Za-z0-9_-]*)="(.*?)"',
            text,
            flags=re.DOTALL,
        )
    }


def extract(text: str, tag: str) -> str:
    """Extract stripped content from one XML-ish tag."""
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
    if not match:
        return text.strip()
    return match.group(1).strip()


def extract_raw(text: str, tag: str) -> str | None:
    """Extract raw content from one XML-ish tag."""
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
    if not match:
        return None
    return match.group(1)
