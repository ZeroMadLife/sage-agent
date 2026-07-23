"""Workspace-scoped durable memory with file-backed storage."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

from core.coding.context import now


@dataclass
class MemoryFact:
    """One durable memory fact with provenance."""

    topic: str = "project-conventions"  # "project-conventions" or "decisions"
    content: str = ""
    source: str = "explicit_remember"  # "explicit_remember", "plan_approved", "run_success"
    source_ref: str = ""  # run_id or plan_path
    created_at: str = ""
    reviewed_at: str = ""
    status: str = "active"  # "active", "proposed", "rejected"


class DurableMemory:
    """File-backed durable memory scoped to one workspace."""

    TOPIC_FILES: ClassVar[dict[str, str]] = {
        "project-conventions": "project-conventions.md",
        "decisions": "decisions.md",
    }

    def __init__(self, storage_root: Path, workspace_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_id):
            raise ValueError("invalid workspace id")
        self._storage_root = _trusted_root(storage_root)
        self._components = ("memory", workspace_id)
        workspace_fd = _open_directory(self._storage_root, self._components)
        os.close(workspace_fd)
        daily_fd = _open_directory(self._storage_root, (*self._components, "daily"))
        os.close(daily_fd)
        self.root = self._storage_root.joinpath(*self._components)

    @property
    def index_path(self) -> Path:
        return self.root / "MEMORY.md"

    def remember(
        self,
        content: str,
        topic: str = "project-conventions",
        source: str = "explicit_remember",
        source_ref: str = "",
    ) -> MemoryFact:
        """Append an explicit memory fact to the daily log and topic file."""
        fact = MemoryFact(
            topic=topic,
            content=content.strip(),
            source=source,
            source_ref=source_ref,
            created_at=now(),
            status="active",
        )
        self._append_daily_log(fact)
        self._append_topic_file(fact)
        self._rebuild_index()
        return fact

    def list_facts(self, topic: str = "") -> list[MemoryFact]:
        """Return facts from topic files. If topic='', return all."""
        facts: list[MemoryFact] = []
        topics = [topic] if topic else list(self.TOPIC_FILES.keys())
        for t in topics:
            facts.extend(self._read_topic_file(t))
        return facts

    def get_index(self) -> str:
        """Return the MEMORY.md index content."""
        directory_fd = self._workspace_fd()
        try:
            return _read_optional_text(directory_fd, "MEMORY.md")
        finally:
            os.close(directory_fd)

    def select_for_context(self, budget: int = 2000) -> str:
        """Return a budgeted string of durable memory for context injection."""
        index = self.get_index()
        if not index:
            return ""
        if len(index) <= budget:
            return index
        return index[:budget] + "\n...[truncated]"

    def propose_dream(self, facts: list[MemoryFact]) -> list[MemoryFact]:
        """Create proposals (does not mutate durable files).

        Returns facts with status='proposed'.
        """
        return [
            MemoryFact(
                topic=f.topic,
                content=f.content,
                source="dream_proposal",
                source_ref=f.source_ref,
                created_at=f.created_at,
                status="proposed",
            )
            for f in facts
        ]

    def approve_dream(self, facts: list[MemoryFact]) -> None:
        """Write approved dream facts to durable files."""
        existing = {(fact.content, fact.source_ref) for fact in self.list_facts()}
        for fact in facts:
            if fact.status == "proposed":
                fact.status = "active"
                key = (fact.content, fact.source_ref)
                if key not in existing:
                    self._append_topic_file(fact)
                    existing.add(key)
        self._rebuild_index()

    def _append_daily_log(self, fact: MemoryFact) -> None:
        entry = f"- [{fact.created_at}] ({fact.topic}) {fact.content}"
        if fact.source_ref:
            entry += f" [ref: {fact.source_ref}]"
        entry += f" [source: {fact.source}]\n"
        daily_fd = _open_directory(self._storage_root, (*self._components, "daily"))
        try:
            _append_text(daily_fd, f"{date.today().isoformat()}.md", entry)
        finally:
            os.close(daily_fd)

    def _append_topic_file(self, fact: MemoryFact) -> None:
        filename = self.TOPIC_FILES.get(fact.topic, "decisions.md")
        entry = json.dumps(
            {
                "content": fact.content,
                "source": fact.source,
                "source_ref": fact.source_ref,
                "created_at": fact.created_at,
            },
            ensure_ascii=False,
        )
        directory_fd = self._workspace_fd()
        try:
            _append_text(directory_fd, filename, entry + "\n")
        finally:
            os.close(directory_fd)

    def _read_topic_file(self, topic: str) -> list[MemoryFact]:
        """Parse a topic file into facts (JSON lines, backward-compat with `- content`)."""
        filename = self.TOPIC_FILES.get(topic, "")
        directory_fd = self._workspace_fd()
        try:
            contents = _read_optional_text(directory_fd, filename)
        finally:
            os.close(directory_fd)
        if not contents:
            return []
        facts: list[MemoryFact] = []
        for line in contents.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                facts.append(
                    MemoryFact(
                        topic=topic,
                        content=data.get("content", ""),
                        source=data.get("source", "explicit_remember"),
                        source_ref=data.get("source_ref", ""),
                        created_at=data.get("created_at", ""),
                        status="active",
                    )
                )
            except json.JSONDecodeError:
                if line.startswith("- "):
                    facts.append(MemoryFact(topic=topic, content=line[2:].strip(), status="active"))
        return facts

    def _rebuild_index(self) -> None:
        lines = ["# Memory Index", ""]
        for topic in self.TOPIC_FILES:
            facts = self._read_topic_file(topic)
            if not facts:
                continue
            lines.append(f"## {topic} ({len(facts)} facts)")
            for fact in facts:
                ref = f" [run: {fact.source_ref[:8]}]" if fact.source_ref else ""
                lines.append(f"  - {fact.content}{ref}")
            lines.append("")
        directory_fd = self._workspace_fd()
        try:
            _replace_text(directory_fd, "MEMORY.md", "\n".join(lines))
        finally:
            os.close(directory_fd)

    def _workspace_fd(self) -> int:
        return _open_directory(self._storage_root, self._components)


def workspace_id_from_path(workspace_root: Path) -> str:
    """Derive a stable workspace identifier from the canonical path."""
    return hashlib.sha256(str(workspace_root.resolve()).encode()).hexdigest()[:16]


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW


def _trusted_root(root: Path) -> Path:
    _reject_untrusted_ancestor_symlinks(root)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = root.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise OSError(f"trusted memory root must not be a symlink: {root}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise OSError(f"trusted memory root is not a directory: {root}")
    root_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        opened = os.fstat(root_fd)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise OSError(f"trusted memory root changed while opening: {root}")
        if opened.st_uid != os.geteuid():
            raise OSError(f"trusted memory root must be owned by the service user: {root}")
        os.fchmod(root_fd, 0o700)
        os.fsync(root_fd)
        resolved = root.resolve(strict=True)
        metadata = resolved.stat()
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise OSError(f"trusted memory root escaped while resolving: {root}")
        return resolved
    finally:
        os.close(root_fd)


def _reject_untrusted_ancestor_symlinks(root: Path) -> None:
    current = Path(root.absolute().anchor)
    for component in root.absolute().parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode) and metadata.st_uid != 0:
            raise OSError(f"untrusted symlink in memory root path: {current}")


def _open_directory(root: Path, components: tuple[str, ...], *, tighten: bool = True) -> int:
    directory_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        for component in components:
            created = False
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory_fd)
                created = True
            except FileExistsError:
                pass
            if created:
                os.fsync(directory_fd)
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise OSError(f"symlink memory directory rejected: {component}") from exc
                raise
            try:
                if tighten:
                    os.fchmod(next_fd, 0o700)
                os.close(directory_fd)
            except Exception:
                os.close(next_fd)
                raise
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def _open_file(directory_fd: int, name: str, flags: int) -> int:
    file_fd = os.open(name, flags | _FILE_FLAGS, 0o600, dir_fd=directory_fd)
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError(f"memory projection must be one regular inode: {name}")
        os.fchmod(file_fd, 0o600)
    except Exception:
        os.close(file_fd)
        raise
    return file_fd


def _append_text(directory_fd: int, name: str, content: str) -> None:
    file_fd = _open_file(directory_fd, name, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    try:
        with os.fdopen(file_fd, "a", encoding="utf-8", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(file_fd)
        metadata = os.fstat(file_fd)
        if metadata.st_nlink != 1:
            raise OSError(f"memory projection link count changed: {name}")
    finally:
        os.close(file_fd)


def _read_optional_text(directory_fd: int, name: str) -> str:
    if not name:
        return ""
    try:
        file_fd = _open_file(directory_fd, name, os.O_RDONLY)
    except FileNotFoundError:
        return ""
    try:
        with os.fdopen(file_fd, "r", encoding="utf-8", closefd=False) as stream:
            contents = stream.read()
        if os.fstat(file_fd).st_nlink != 1:
            raise OSError(f"memory projection link count changed: {name}")
        return contents
    finally:
        os.close(file_fd)


def _replace_text(directory_fd: int, name: str, content: str) -> None:
    try:
        existing_fd = _open_file(directory_fd, name, os.O_RDONLY)
    except FileNotFoundError:
        existing_fd = None
    if existing_fd is not None:
        os.close(existing_fd)
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    temp_fd = _open_file(
        directory_fd,
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(temp_fd)
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory_fd)
        raise
    finally:
        os.close(temp_fd)
