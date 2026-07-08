"""File and search tools for the coding agent."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.coding.tools.base import ToolContext, ToolResult
from core.coding.tools.registry import register_tool
from core.coding.tools.schemas import (
    ListFilesArgs,
    PatchFileArgs,
    ReadFileArgs,
    SearchArgs,
    WriteFileArgs,
)
from core.coding.workspace import IGNORED_PATH_NAMES, WorkspaceContext, clip


@register_tool(
    name="list_files",
    description="List files in the workspace.",
    schema={"path": "str='.'"},
    schema_model=ListFilesArgs,
    risky=False,
    category="file",
)
def list_files(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """List workspace files with stable markers."""
    _ = tool_context
    path = workspace.path(str(args.get("path", ".")))
    entries = [
        item
        for item in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        if item.name not in IGNORED_PATH_NAMES
    ]
    lines = [
        f"{'[D]' if entry.is_dir() else '[F]'} {entry.relative_to(workspace.root)}"
        for entry in entries[:200]
    ]
    return ToolResult(content="\n".join(lines) or "(empty)")


@register_tool(
    name="read_file",
    description="Read a UTF-8 file by line range.",
    schema={"path": "str", "start": "int=1", "end": "int=200"},
    schema_model=ReadFileArgs,
    risky=False,
    category="file",
)
def read_file(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Read a UTF-8 text file by line range."""
    _ = tool_context
    path = workspace.path(str(args["path"]))
    start = int(args.get("start", 1))
    end = int(args.get("end", 200))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "\n".join(
        f"{number:>4}: {line}" for number, line in enumerate(lines[start - 1 : end], start=start)
    )
    workspace.mark_read(path)
    return ToolResult(content=clip(f"# {path.relative_to(workspace.root)}\n{body}"))


@register_tool(
    name="search",
    description="Search the workspace using rg or a Python fallback.",
    schema={"pattern": "str", "path": "str='.'"},
    schema_model=SearchArgs,
    risky=False,
    category="file",
)
def search(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Search for a pattern under the workspace."""
    _ = tool_context
    pattern = str(args["pattern"])
    path = workspace.path(str(args.get("path", ".")))

    if shutil.which("rg"):
        target = "." if path == workspace.root else str(path.relative_to(workspace.root))
        result = subprocess.run(
            ["rg", "-n", "--smart-case", "--max-count", "200", pattern, target],
            cwd=workspace.root,
            capture_output=True,
            text=True,
            check=False,
        )
        content = result.stdout.strip() or result.stderr.strip() or "(no matches)"
        return ToolResult(content=clip(content))

    matches: list[str] = []
    files = [path] if path.is_file() else _walk_search_files(path, workspace.root)
    for file_path in files:
        for number, line in enumerate(
            file_path.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        ):
            if pattern.lower() in line.lower():
                matches.append(f"{file_path.relative_to(workspace.root)}:{number}:{line}")
                if len(matches) >= 200:
                    return ToolResult(content=clip("\n".join(matches)))
    return ToolResult(content=clip("\n".join(matches) or "(no matches)"))


@register_tool(
    name="write_file",
    description="Write a text file.",
    schema={"path": "str", "content": "str"},
    schema_model=WriteFileArgs,
    risky=True,
    category="file",
)
def write_file(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Write a text file under the workspace."""
    _ = tool_context
    path = workspace.path(str(args["path"]))
    content = str(args["content"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    workspace.mark_self_authored(path)
    return ToolResult(content=f"wrote {path.relative_to(workspace.root)} ({len(content)} chars)")


@register_tool(
    name="patch_file",
    description="Replace one exact text block in a file.",
    schema={"path": "str", "old_text": "str", "new_text": "str"},
    schema_model=PatchFileArgs,
    risky=True,
    category="file",
)
def patch_file(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Replace one exact text block in a file."""
    _ = tool_context
    path = workspace.path(str(args["path"]))
    old_text = str(args["old_text"])
    new_text = str(args["new_text"])
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    workspace.mark_self_authored(path)
    return ToolResult(content=f"patched {path.relative_to(workspace.root)}")


def _walk_search_files(path: Path, root: Path) -> list[Path]:
    return [
        item
        for item in path.rglob("*")
        if item.is_file()
        and not any(part in IGNORED_PATH_NAMES for part in item.relative_to(root).parts)
    ]
