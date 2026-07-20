"""Project Sage runtime metadata into the neutral Capability Registry."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from langchain_core.tools import BaseTool
from sage_harness import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilitySurface,
    McpCatalogSnapshot,
)

from core.coding.skills import Skill
from core.coding.tools.base import RegisteredTool

_HOME_PATH = re.compile(r"(?:(?:/Users|/home)/[^\s,;]+|[A-Za-z]:\\Users\\[^\s,;]+)")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password)\s*[:=]\s*[^\s,;]+"
)
_UNSAFE_ID = re.compile(r"[^a-z0-9_.-]+")
_ALL_SURFACES: tuple[CapabilitySurface, ...] = ("coding", "growth", "knowledge")


def _public_text(value: object, *, maximum: int = 1000) -> str:
    text = _HOME_PATH.sub("[path]", str(value or ""))
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = " ".join(text.split())[:maximum]
    return text or "No public description available."


def _segment(value: object) -> str:
    normalized = _UNSAFE_ID.sub("-", str(value or "").strip().lower()).strip("-.")
    return normalized[:128] or "unknown"


def local_tool_capability_id(name: object) -> str:
    """Return the stable Registry ID for one application-owned tool name."""
    return f"local:{_segment(name)}"


def mcp_tool_capability_id(tool_id: object) -> str:
    """Return the stable Registry ID for one scoped MCP descriptor identity."""
    parts = tuple(_segment(part) for part in str(tool_id).split(":"))
    return ":".join(("mcp", *parts))


def _revision(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _tool_schema(tool: object) -> dict[str, object]:
    if isinstance(tool, RegisteredTool):
        return dict(tool.schema)
    if isinstance(tool, BaseTool):
        schema = tool.args_schema
        if isinstance(schema, dict):
            return dict(schema)
        if schema is not None and hasattr(schema, "model_json_schema"):
            value = schema.model_json_schema()
            return dict(value) if isinstance(value, dict) else {}
        args = getattr(tool, "args", {})
        return dict(args) if isinstance(args, dict) else {}
    schema = getattr(tool, "schema", {})
    return dict(schema) if isinstance(schema, dict) else {}


def _tool_descriptor(name: str, tool: object) -> CapabilityDescriptor:
    metadata = getattr(tool, "metadata", None)
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    category = _segment(getattr(tool, "category", metadata.get("category", "general")))
    risky = bool(getattr(tool, "risky", metadata.get("risky", False)))
    requires_approval = bool(
        getattr(tool, "requires_approval", metadata.get("requires_approval", risky))
    )
    timeout = float(getattr(tool, "timeout", metadata.get("timeout", 30.0)) or 30.0)
    deferred = bool(getattr(tool, "deferred", metadata.get("deferred", False)))
    description = _public_text(getattr(tool, "description", ""))
    schema = _tool_schema(tool)
    surfaces: tuple[CapabilitySurface, ...] = {
        "knowledge": _ALL_SURFACES,
        "memory": ("coding", "growth"),
        "travel": ("coding", "growth"),
        "meta": _ALL_SURFACES,
    }.get(category, ("coding",))
    return CapabilityDescriptor(
        capability_id=local_tool_capability_id(name),
        name=_public_text(name, maximum=128),
        origin="local",
        kind="tool",
        revision=_revision(
            {
                "name": name,
                "description": description,
                "category": category,
                "schema": schema,
                "risky": risky,
                "approval": requires_approval,
                "timeout": timeout,
                "deferred": deferred,
            }
        ),
        description=description,
        surfaces=surfaces,
        risk="high" if risky else "low",
        permission="approval" if requires_approval else "none",
        deferred=deferred,
        remote_content=False,
        availability="available",
        timeout_seconds=min(max(timeout, 0.001), 3600.0),
        tags=(category,),
    )


def _skill_descriptor(skill: Skill) -> CapabilityDescriptor:
    source = _segment(skill.source)
    name = _segment(skill.name)
    description = _public_text(skill.description)
    allowed_tools = tuple(sorted(_segment(item) for item in skill.allowed_tools))
    return CapabilityDescriptor(
        capability_id=f"skill:{source}:{name}",
        name=_public_text(skill.name, maximum=128),
        origin="skill",
        kind="workflow",
        revision=_revision(
            {
                "source": source,
                "name": name,
                "description": description,
                "prompt_hash": hashlib.sha256(skill.prompt.encode("utf-8")).hexdigest()[:16],
                "allowed_tools": allowed_tools,
                "user_invocable": skill.user_invocable,
            }
        ),
        description=description,
        surfaces=_ALL_SURFACES,
        risk="medium" if allowed_tools else "low",
        permission="runtime" if allowed_tools else "none",
        deferred=True,
        remote_content=False,
        availability="available" if skill.user_invocable else "disabled",
        timeout_seconds=30.0,
        tags=("skill", source),
    )


def _mcp_descriptors(catalog: McpCatalogSnapshot) -> list[CapabilityDescriptor]:
    statuses = {server.name: server.status for server in catalog.servers}
    descriptors: list[CapabilityDescriptor] = []
    for tool in catalog.tools:
        status = statuses.get(tool.server_name, "stale")
        availability = {
            "connected": "available",
            "degraded": "degraded",
            "stale": "stale",
        }.get(status, "unavailable")
        descriptors.append(
            CapabilityDescriptor(
                capability_id=mcp_tool_capability_id(tool.tool_id),
                name=_public_text(tool.name, maximum=128),
                origin="mcp",
                kind="tool",
                revision=_revision(
                    {
                        "config_revision": catalog.revision,
                        "schema_hash": tool.schema_hash,
                        "remote_content": tool.remote_content,
                    }
                ),
                description=_public_text(tool.description),
                surfaces=_ALL_SURFACES,
                risk="medium",
                permission="runtime",
                deferred=True,
                remote_content=tool.remote_content,
                availability=availability,  # type: ignore[arg-type]
                timeout_seconds=30.0,
                tags=("mcp", _segment(tool.server_name)),
            )
        )
    return descriptors


def _explore_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id="subagent:explore",
        name="Explore",
        origin="subagent",
        kind="delegate",
        revision="explore-read-only-v1",
        description="Read-only child agent for bounded workspace exploration.",
        surfaces=_ALL_SURFACES,
        risk="low",
        permission="runtime",
        deferred=True,
        remote_content=False,
        availability="available",
        timeout_seconds=300.0,
        tags=("explore", "read-only"),
    )


def _research_descriptor(*, available: bool) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id="subagent:research",
        name="Research",
        origin="subagent",
        kind="delegate",
        revision="research-evidence-v1",
        description=("Read-only child agent for bounded Knowledge and public web research."),
        surfaces=_ALL_SURFACES,
        risk="medium",
        permission="runtime",
        deferred=True,
        remote_content=True,
        availability="available" if available else "unavailable",
        timeout_seconds=180.0,
        tags=("research", "read-only", "evidence"),
    )


def _practice_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id="subagent:practice",
        name="Practice",
        origin="subagent",
        kind="delegate",
        revision="practice-code-test-v1",
        description=(
            "Bounded workspace exercise child that can edit and run deterministic tests "
            "under the parent policy, approval, and sandbox."
        ),
        surfaces=_ALL_SURFACES,
        risk="high",
        permission="approval",
        deferred=True,
        remote_content=False,
        availability="available",
        timeout_seconds=300.0,
        tags=("practice", "workspace", "evidence"),
    )


def _web_search_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id="web:search",
        name="search_web",
        origin="web",
        kind="tool",
        revision="web-search-current-turn-v1",
        description=(
            "Search public web sources and return bounded current-turn citation excerpts."
        ),
        surfaces=_ALL_SURFACES,
        risk="medium",
        permission="runtime",
        deferred=True,
        remote_content=True,
        availability="available",
        timeout_seconds=30.0,
        tags=("web", "search", "evidence"),
    )


def _web_fetch_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id="web:fetch",
        name="fetch_web",
        origin="web",
        kind="tool",
        revision="web-fetch-current-turn-v1",
        description="Fetch one public HTTPS HTML page into bounded current-turn evidence.",
        surfaces=_ALL_SURFACES,
        risk="medium",
        permission="runtime",
        deferred=True,
        remote_content=True,
        availability="available",
        timeout_seconds=30.0,
        tags=("web", "fetch", "evidence"),
    )


def _web_save_source_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id="web:save-source",
        name="save_web_source",
        origin="web",
        kind="tool",
        revision="web-source-proposal-v1",
        description="Propose a fetched web artifact for user-reviewed Knowledge ingestion.",
        surfaces=_ALL_SURFACES,
        risk="medium",
        permission="runtime",
        deferred=True,
        remote_content=False,
        availability="available",
        timeout_seconds=30.0,
        tags=("web", "knowledge", "proposal"),
    )


def build_sage_capability_registry(
    *,
    tools: Mapping[str, object],
    skills: Sequence[Skill],
    mcp_catalog: McpCatalogSnapshot | None = None,
    web_search_available: bool = False,
    web_fetch_available: bool = False,
    web_source_proposal_available: bool = False,
    research_subagent_available: bool = False,
    practice_subagent_available: bool = False,
) -> CapabilityRegistry:
    """Build a public catalog from current runtime-owned source metadata."""
    descriptors = [_tool_descriptor(name, tool) for name, tool in tools.items()]
    descriptors.extend(_skill_descriptor(skill) for skill in skills)
    if mcp_catalog is not None:
        descriptors.extend(_mcp_descriptors(mcp_catalog))
    descriptors.append(_explore_descriptor())
    descriptors.append(_research_descriptor(available=research_subagent_available))
    if practice_subagent_available:
        descriptors.append(_practice_descriptor())
    if web_search_available:
        descriptors.append(_web_search_descriptor())
    if web_fetch_available:
        descriptors.append(_web_fetch_descriptor())
    if web_source_proposal_available:
        descriptors.append(_web_save_source_descriptor())
    return CapabilityRegistry(descriptors)


__all__ = [
    "build_sage_capability_registry",
    "local_tool_capability_id",
    "mcp_tool_capability_id",
]
