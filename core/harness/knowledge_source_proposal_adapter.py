"""Proposal-only bridge from private run evidence to Knowledge ingestion."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from sage_harness import (
    KnowledgeSourceProposalPort,
    KnowledgeSourceProposalReceipt,
)

from core.coding.persistence.tool_result_store import ToolResultStore
from core.coding.runtime import CodingRuntime
from core.knowledge import KnowledgeSourceRoot
from core.knowledge.jobs import KnowledgeJobService
from core.knowledge.source_proposals import (
    KnowledgeSourceProposal,
    KnowledgeSourceProposalRepository,
)

_CONTENT_HASH = re.compile(r"^[a-f0-9]{64}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_ROOT_ID = "web-evidence"


class CodingKnowledgeSourceProposalService:
    """Own artifact verification, immutable snapshots, and durable job creation."""

    def __init__(
        self,
        repository: KnowledgeSourceProposalRepository,
        job_service: KnowledgeJobService,
        *,
        coding_storage_root: Path,
    ) -> None:
        self.repository = repository
        self.job_service = job_service
        self.coding_storage_root = coding_storage_root.resolve()
        self._knowledge_root = job_service.store.workspace_root.resolve()
        self.snapshot_root = self._knowledge_root / ".sage" / "sources" / _ROOT_ID

    async def prepare(self) -> None:
        """Prepare storage and restore only sources already materialized by approval."""
        _prepare_private_directory_tree(
            self._knowledge_root,
            (".sage", "sources", _ROOT_ID),
        )
        if await self.repository.has_materialized_source(
            workspace_id=self.job_service.workspace_id
        ):
            await self._register_source_root()

    async def _register_source_root(self) -> None:
        await self.job_service.register_source_root(
            KnowledgeSourceRoot(
                root_id=_ROOT_ID,
                kind="web",
                label="Confirmed web evidence",
                path=self.snapshot_root,
            )
        )

    async def propose(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        thread_id: str,
        run_id: str,
        artifact_ref: str,
        reason: str,
        evidence_refs: Sequence[str],
    ) -> KnowledgeSourceProposal:
        await self.prepare()
        normalized_reason = " ".join(reason.split())
        if not normalized_reason or len(normalized_reason) > 1_000:
            raise ValueError("source proposal reason must be between 1 and 1000 characters")
        normalized_refs = _evidence_refs(evidence_refs)
        content, metadata = self._verified_artifact(thread_id, run_id, artifact_ref)
        return await self.repository.create(
            workspace_id=workspace_id,
            owner_id=owner_id,
            thread_id=thread_id,
            run_id=run_id,
            artifact_ref=artifact_ref,
            canonical_url=str(metadata["canonical_url"]),
            title=str(metadata["title"]),
            media_type=str(metadata["media_type"]),
            retrieved_at=str(metadata["retrieved_at"]),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            reason=normalized_reason,
            evidence_refs=normalized_refs,
        )

    async def approve(
        self,
        proposal_id: str,
        *,
        owner_id: str,
        workspace_id: str,
        thread_id: str,
        expected_revision: int,
        decided_by: str,
    ) -> KnowledgeSourceProposal:
        await self.prepare()
        proposal = await self.repository.claim_applying(
            proposal_id,
            workspace_id=workspace_id,
            owner_id=owner_id,
            thread_id=thread_id,
            expected_revision=expected_revision,
            decided_by=decided_by,
        )
        try:
            content, metadata = self._verified_artifact(
                proposal.thread_id,
                proposal.run_id,
                proposal.artifact_ref,
            )
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != proposal.content_hash:
                raise ValueError("source artifact content hash changed")
            if str(metadata["canonical_url"]) != proposal.canonical_url:
                raise ValueError("source artifact URL changed")
            relative_path = self._materialize(proposal, content)
            await self._register_source_root()
            job = await self.job_service.create_batch(_ROOT_ID, proposal.proposal_id)
            await self.repository.attach_job(
                proposal.proposal_id,
                job_id=job.job_id,
                target_relative_path=relative_path,
            )
            return await self.repository.mark_approved(proposal.proposal_id)
        except Exception as exc:
            await self.repository.mark_failed(proposal.proposal_id, _safe_error(exc))
            raise

    async def reject(
        self,
        proposal_id: str,
        *,
        owner_id: str,
        workspace_id: str,
        thread_id: str,
        expected_revision: int,
        decided_by: str,
    ) -> KnowledgeSourceProposal:
        return await self.repository.reject(
            proposal_id,
            workspace_id=workspace_id,
            owner_id=owner_id,
            thread_id=thread_id,
            expected_revision=expected_revision,
            decided_by=decided_by,
        )

    def _verified_artifact(
        self, thread_id: str, run_id: str, artifact_ref: str
    ) -> tuple[str, dict[str, object]]:
        store = ToolResultStore(self.coding_storage_root, thread_id, run_id)
        content = store.read(artifact_ref)
        metadata = store.read_metadata(artifact_ref)
        required = {
            "artifact_kind",
            "canonical_url",
            "title",
            "retrieved_at",
            "content_hash",
            "media_type",
        }
        if not required.issubset(metadata) or metadata["artifact_kind"] != "web_fetch":
            raise ValueError("artifact is not a verified web fetch")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if not _CONTENT_HASH.fullmatch(str(metadata["content_hash"])) or digest != metadata[
            "content_hash"
        ]:
            raise ValueError("artifact content hash does not match metadata")
        url = urlsplit(str(metadata["canonical_url"]))
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("artifact canonical URL is invalid")
        title = " ".join(str(metadata["title"]).split())
        if not title or len(title) > 300:
            raise ValueError("artifact title is invalid")
        if str(metadata["media_type"]) not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("artifact media type is unsupported")
        return content, metadata

    def _materialize(self, proposal: KnowledgeSourceProposal, content: str) -> str:
        directory = self.snapshot_root / proposal.proposal_id
        directory.mkdir(parents=False, exist_ok=True, mode=0o700)
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.snapshot_root):
            raise ValueError("knowledge web source directory is unsafe")
        target = directory / "source.md"
        rendered = _snapshot_markdown(proposal, content)
        if target.exists():
            stat = target.lstat()
            if (
                target.is_symlink()
                or not target.is_file()
                or stat.st_nlink != 1
                or target.read_text(encoding="utf-8") != rendered
            ):
                raise ValueError("knowledge web source snapshot conflicts")
            return f"{proposal.proposal_id}/source.md"
        temporary = directory / f".source.{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                payload = rendered.encode("utf-8")
                view = memoryview(payload)
                while view:
                    written = os.write(fd, view)
                    if written == 0:
                        raise OSError("short write while materializing source")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return f"{proposal.proposal_id}/source.md"


class CodingKnowledgeSourceProposalPort(KnowledgeSourceProposalPort):
    """Bind proposal creation to one active runtime and run artifact scope."""

    def __init__(
        self,
        runtime: CodingRuntime,
        run_id: str,
        service: CodingKnowledgeSourceProposalService | None,
    ) -> None:
        self.runtime = runtime
        self.run_id = run_id
        self.service = service

    @property
    def available(self) -> bool:
        return self.service is not None

    async def propose(
        self,
        thread_id: str,
        run_id: str,
        artifact_ref: str,
        *,
        reason: str,
        evidence_refs: Sequence[str] = (),
    ) -> KnowledgeSourceProposalReceipt:
        if self.service is None:
            raise RuntimeError("knowledge source proposal service is unavailable")
        if thread_id != self.runtime.session_id or run_id != self.run_id:
            raise PermissionError("knowledge source proposal scope does not match active run")
        proposal = await self.service.propose(
            owner_id=self.runtime.owner_user_id or "local",
            workspace_id=self.service.job_service.workspace_id,
            thread_id=thread_id,
            run_id=run_id,
            artifact_ref=artifact_ref,
            reason=reason,
            evidence_refs=evidence_refs,
        )
        return KnowledgeSourceProposalReceipt(
            proposal_id=proposal.proposal_id,
            thread_id=proposal.thread_id,
            run_id=proposal.run_id,
            status=proposal.status,  # type: ignore[arg-type]
            revision=proposal.revision,
            source_kind=proposal.source_kind,
            title=proposal.title,
            content_hash=proposal.content_hash,
        )


def _evidence_refs(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        item = value.strip()
        if not _REFERENCE.fullmatch(item):
            raise ValueError("evidence reference is invalid")
        if item not in normalized:
            normalized.append(item)
    if len(normalized) > 20:
        raise ValueError("source proposal accepts at most 20 evidence references")
    return tuple(normalized)


def _snapshot_markdown(proposal: KnowledgeSourceProposal, content: str) -> str:
    title = proposal.title.replace("\n", " ").strip()
    return (
        f"# {title}\n\n"
        f"> Source: {proposal.canonical_url}\n"
        f"> Retrieved: {proposal.retrieved_at}\n"
        f"> Content SHA-256: {proposal.content_hash}\n\n"
        f"{content.strip()}\n"
    )


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ValueError | FileNotFoundError | PermissionError):
        return str(exc)[:1000]
    return f"{type(exc).__name__}: source proposal approval failed"


def _prepare_private_directory_tree(root: Path, components: tuple[str, ...]) -> None:
    current = root
    for component in components:
        current = current / component
        try:
            stat = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            stat = current.lstat()
        if current.is_symlink() or not current.is_dir() or stat.st_nlink < 1:
            raise ValueError("knowledge web source path must be a private directory tree")
        try:
            current.resolve(strict=True).relative_to(root)
        except ValueError as exc:
            raise ValueError("knowledge web source path escapes its workspace") from exc


__all__ = [
    "CodingKnowledgeSourceProposalPort",
    "CodingKnowledgeSourceProposalService",
]
