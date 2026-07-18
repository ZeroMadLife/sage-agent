"""Sage application projections into the neutral Capability Registry."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from sage_harness import McpCatalogSnapshot, McpScope, McpServerReference, McpToolDescriptor

from core.coding.context import WorkspaceContext
from core.coding.skills import Skill
from core.coding.tools.registry import build_tool_registry
from core.harness.capability_adapter import build_sage_capability_registry


def test_adapter_projects_current_sources_without_paths_or_secrets(tmp_path) -> None:  # type: ignore[no-untyped-def]
    workspace = WorkspaceContext(tmp_path)
    tools = build_tool_registry(workspace)
    skills = [
        Skill(
            name="review",
            source="project",
            description=("Review /Users/example/private/repo with api_key=top-secret"),
            prompt="Review carefully",
            skill_root="/Users/example/private/repo/skills/review",
            allowed_tools=("read_file",),
        ),
        Skill(
            name="hidden",
            source="user",
            description="Internal only",
            prompt="Hidden",
            user_invocable=False,
        ),
    ]
    remote = McpToolDescriptor.from_schema(
        tool_id="github:search_code",
        server_name="github",
        name="github_search_code",
        original_name="search_code",
        description="Search token=remote-secret under /home/private/repo",
        schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    mcp = McpCatalogSnapshot(
        revision="mcp-r1",
        scope=McpScope("owner", "workspace", "thread"),
        servers=(
            McpServerReference(
                name="github",
                transport="streamable_http",
                status="connected",
                tool_names=("github_search_code",),
            ),
        ),
        tools=(remote,),
        catalog_hash="catalog-r1",
    )

    registry = build_sage_capability_registry(tools=tools, skills=skills, mcp_catalog=mcp)
    rendered = repr([item.as_dict() for item in registry.list()])

    ids = {item.capability_id for item in registry.list()}
    assert "local:read_file" in ids
    assert "mcp:github:search_code" in ids
    assert "skill:project:review" in ids
    assert "skill:user:hidden" in ids
    assert "subagent:explore" in ids
    assert registry.get("local:run_shell").permission == "approval"
    assert registry.get("local:read_file").permission == "none"
    assert registry.get("skill:user:hidden").availability == "disabled"
    assert registry.get("mcp:github:search_code").remote_content is True
    assert "/Users/example" not in rendered
    assert "/home/private" not in rendered
    assert "top-secret" not in rendered
    assert "remote-secret" not in rendered
    assert "skill_root" not in rendered


def test_adapter_revision_tracks_tool_schema_without_exposing_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    workspace = WorkspaceContext(tmp_path)
    tools = build_tool_registry(workspace)
    first = build_sage_capability_registry(tools=tools, skills=[])

    changed = dict(tools)
    original = changed["read_file"]
    changed["read_file"] = StructuredTool.from_function(
        func=lambda path: path,
        name=original.name,
        description=original.description,
    )

    second = build_sage_capability_registry(tools=changed, skills=[])

    assert first.get("local:read_file").revision != second.get("local:read_file").revision
    assert "schema" not in first.get("local:read_file").as_dict()


def test_adapter_exposes_web_search_only_when_server_port_is_available(tmp_path) -> None:  # type: ignore[no-untyped-def]
    workspace = WorkspaceContext(tmp_path)
    tools = build_tool_registry(workspace)

    disabled = build_sage_capability_registry(tools=tools, skills=[])
    enabled = build_sage_capability_registry(
        tools=tools,
        skills=[],
        web_search_available=True,
    )

    assert disabled.get("web:search") is None
    descriptor = enabled.get("web:search")
    assert descriptor is not None
    assert descriptor.deferred is True
    assert descriptor.remote_content is True
    assert descriptor.permission == "runtime"
