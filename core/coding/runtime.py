"""Runtime assembly for a web coding-agent session."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from inspect import signature
from pathlib import Path
from typing import Any, cast

from core.coding.context import (
    IGNORED_PATH_NAMES,
    CompactionCheckpoint,
    CompactionResult,
    CompactManager,
    ContextBusyError,
    ContextController,
    ContextManager,
    ContextPolicy,
    ContextProjector,
    ModelCapabilityRegistry,
    PreparedContext,
    StructuredSummarizer,
    TokenCounter,
    WorkspaceContext,
    WorkspaceDiffTracker,
    now,
)
from core.coding.engine.engine import Engine
from core.coding.engine.events import (
    CancelledEvent,
    ContextCompactionCompletedEvent,
    ContextCompactionFailedEvent,
    ContextCompactionStartedEvent,
    ErrorEvent,
    PlanReadyForReviewEvent,
    RunEventBase,
    RunFinishedEvent,
    RuntimeModeChangedEvent,
    TurnFinishedEvent,
    TurnStartedEvent,
    WorkspaceDiffReadyEvent,
    event_to_dict,
)
from core.coding.memory import MemoryManager
from core.coding.multiagent import WorkerManager
from core.coding.persistence import (
    CodingSessionStore,
    CompactionStore,
    RunStore,
    SessionEventBus,
    TodoLedger,
    TranscriptItem,
    TranscriptStore,
)
from core.coding.plan_mode import PlanModeManager
from core.coding.plan_review import PlanReviewManager
from core.coding.skills import SkillRegistry
from core.coding.tool_executor import (
    ApprovalManager,
    PermissionChecker,
    PermissionMode,
    ToolPolicyChecker,
)
from core.coding.tools.base import ToolContext
from core.coding.tools.registry import build_tool_registry
from core.coding.usage_store import UsageSample, UsageStore
from core.harness import RuntimeProfile, normalize_runtime_profile

logger = logging.getLogger(__name__)


class CodingRuntime:
    """A complete coding-agent session state."""

    def __init__(
        self,
        session_id: str,
        workspace_root: Path | str,
        model: Any,
        storage_root: Path | str,
        model_factory: Callable[..., Any] | None = None,
        approval_policy: str = "auto",
        session_state: dict[str, Any] | None = None,
        save_on_init: bool = True,
        permission_mode: PermissionMode = "default",
        context_policy: ContextPolicy | None = None,
        model_capabilities: ModelCapabilityRegistry | None = None,
        checkpoint_anchor_key: bytes | None = None,
        context_controller: ContextController | None = None,
        model_spec: str = "",
        reasoning_mode: str = "off",
        model_reasoning_modes: Mapping[str, tuple[str, ...] | list[str]] | None = None,
        usage_store: UsageStore | None = None,
        owner_user_id: str | None = None,
        knowledge_store: Any | None = None,
        runtime_profile: RuntimeProfile | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace = WorkspaceContext(root=Path(workspace_root))
        self.model = model
        self.model_factory = model_factory or (lambda: model)
        self.storage_root = Path(storage_root)
        self.session_store = CodingSessionStore(self.storage_root / "sessions")
        self.run_store = RunStore(self.storage_root, session_id=session_id)
        self.transcript_store = TranscriptStore(self.storage_root, session_id)
        self.compaction_store = CompactionStore(
            self.storage_root, checkpoint_anchor_key=checkpoint_anchor_key
        )
        self.diff_tracker = WorkspaceDiffTracker(self.workspace.root)
        self.session_event_bus = SessionEventBus(
            session_id=session_id,
            path=self.session_store.event_path(session_id),
        )
        self.session = (
            dict(session_state)
            if session_state is not None
            else {
                "id": session_id,
                "workspace_root": str(self.workspace.root),
                "created_at": now(),
                "updated_at": now(),
                "history": [],
                "runtime_mode": {"mode": "default"},
                "runtime_profile": normalize_runtime_profile(runtime_profile),
                "todos": {"next_id": 1, "items": []},
            }
        )
        self.session["id"] = session_id
        self.session["workspace_root"] = str(self.workspace.root)
        persisted_owner = str(self.session.get("owner_user_id", "")).strip()
        requested_owner = (owner_user_id or "").strip()
        self.owner_user_id = persisted_owner or requested_owner or None
        if persisted_owner and requested_owner and persisted_owner != requested_owner:
            raise ValueError("coding session owner does not match persisted state")
        self.session.setdefault("history", [])
        self.session.setdefault("runtime_mode", {"mode": "default"})
        persisted_runtime_profile = normalize_runtime_profile(self.session.get("runtime_profile"))
        if runtime_profile is not None and persisted_runtime_profile != runtime_profile:
            raise ValueError("coding session runtime profile does not match persisted state")
        self._runtime_profile = persisted_runtime_profile
        self.session["runtime_profile"] = self._runtime_profile
        self.session.setdefault("todos", {"next_id": 1, "items": []})
        self.session.setdefault("activated_tools", [])
        self.activated_tools = {
            str(name) for name in self.session.get("activated_tools", []) if str(name).strip()
        }
        self.model_spec = str(self.session.get("model_spec", model_spec))
        self.model_reasoning_modes = {
            str(model_id): tuple(str(mode) for mode in modes)
            for model_id, modes in (model_reasoning_modes or {}).items()
        }
        persisted_reasoning_mode = str(self.session.get("reasoning_mode", reasoning_mode))
        self.reasoning_mode = self._resolve_reasoning_mode(
            self.model_spec, persisted_reasoning_mode
        )
        self.usage_store = usage_store
        self.todo_ledger = TodoLedger(self.session["todos"])
        self.plan_mode = PlanModeManager(self.workspace.root)
        self._restore_plan_mode(self.session["runtime_mode"])
        self.context_manager = ContextManager()
        self.approval_policy = approval_policy
        persisted_permission_mode = str(self.session.get("permission_mode", permission_mode))
        self.permission_mode: PermissionMode = cast(
            PermissionMode,
            persisted_permission_mode
            if persisted_permission_mode in {"default", "accept_edits", "auto", "plan"}
            else "default",
        )
        self.approval_manager = ApprovalManager()
        self.plan_review_manager = PlanReviewManager()
        self.stop_requested = False
        self.active_run_id: str | None = None
        self._context_operation_lock = asyncio.Lock()
        self.runtime_mode = self.plan_mode.mode
        self.permission_checker = self._permission_checker()
        self.policy_checker = ToolPolicyChecker(self.workspace)
        self.worker_manager = WorkerManager(self.workspace, self._current_model_factory)
        self.tool_context = ToolContext(
            runtime=self,
            todo_ledger=self.todo_ledger,
            worker_manager=self.worker_manager,
            knowledge_store=knowledge_store,
        )
        self.tools = build_tool_registry(
            self.workspace,
            tool_context=self.tool_context,
            activated_tools=self.activated_tools,
        )
        self.skill_registry = SkillRegistry(root=self.workspace.root)
        self.memory_manager = MemoryManager(self.storage_root, self.workspace.root)
        self._turn_id = ""
        self._backfill_transcript()
        self.model_capabilities = model_capabilities or ModelCapabilityRegistry()
        self.context_policy = (
            context_policy
            or self.model_capabilities.resolve(self.model_spec)
            or self.model_capabilities.resolve(model)
        )
        self.context_controller = context_controller
        if self.context_controller is None and self.context_policy is not None:
            self.context_controller = self._build_context_controller(
                model, self.context_policy
            )
        elif self.context_controller is not None:
            self.context_controller.lifecycle_sink = self._context_lifecycle_sink
        self._restore_context_state()
        if save_on_init:
            self._save_session()

    @classmethod
    def resume(
        cls,
        session_id: str,
        model: Any,
        storage_root: Path | str,
        model_factory: Callable[..., Any] | None = None,
        approval_policy: str = "auto",
        context_policy: ContextPolicy | None = None,
        model_capabilities: ModelCapabilityRegistry | None = None,
        checkpoint_anchor_key: bytes | None = None,
        model_spec: str = "",
        reasoning_mode: str = "off",
        model_reasoning_modes: Mapping[str, tuple[str, ...] | list[str]] | None = None,
        usage_store: UsageStore | None = None,
        knowledge_store: Any | None = None,
        runtime_profile: RuntimeProfile | None = None,
    ) -> CodingRuntime:
        """Rehydrate a persisted coding runtime for a new WebSocket connection."""
        storage_path = Path(storage_root)
        store = CodingSessionStore(storage_path / "sessions")
        session_state = store.load(session_id)
        workspace_root = Path(str(session_state.get("workspace_root", "")))
        if not workspace_root:
            raise ValueError("persisted session is missing workspace_root")
        return cls(
            session_id=session_id,
            workspace_root=workspace_root,
            model=model,
            storage_root=storage_path,
            model_factory=model_factory,
            approval_policy=approval_policy,
            session_state=session_state,
            save_on_init=False,
            context_policy=context_policy,
            model_capabilities=model_capabilities,
            checkpoint_anchor_key=checkpoint_anchor_key,
            model_spec=str(session_state.get("model_spec", model_spec)),
            reasoning_mode=str(session_state.get("reasoning_mode", reasoning_mode)),
            model_reasoning_modes=model_reasoning_modes,
            usage_store=usage_store,
            knowledge_store=knowledge_store,
            runtime_profile=runtime_profile,
        )

    @property
    def runtime_profile(self) -> RuntimeProfile:
        """Return the creation-time runtime profile; it has no mutable setter."""
        return self._runtime_profile

    def list_files(self, path: str = ".") -> list[dict[str, Any]]:
        """Return directory entries (dirs first, then files), workspace-safe."""
        target = self.workspace.path(path)
        if not target.is_dir():
            raise ValueError("path is not a directory")
        entries = [
            item
            for item in sorted(
                target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())
            )
            if item.name not in IGNORED_PATH_NAMES
        ]
        return [{"name": item.name, "is_dir": item.is_dir()} for item in entries[:200]]

    def read_file(self, path: str) -> dict[str, Any]:
        """Return file content preview (workspace-safe)."""
        target = self.workspace.path(path)
        if not target.is_file():
            raise ValueError("path is not a file")
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "content": content, "lines": len(content.splitlines())}

    def git_status(self) -> dict[str, Any]:
        """Return branch + dirty file list (best-effort, 3s timeout)."""
        root = self.workspace.root
        try:
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=3,
            )
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {"is_git": False, "branch": "", "dirty_count": 0, "changed_files": []}

        if branch_result.returncode != 0 and status_result.returncode != 0:
            return {"is_git": False, "branch": "", "dirty_count": 0, "changed_files": []}

        branch = branch_result.stdout.strip()
        changed_files = [
            line[3:].strip() for line in status_result.stdout.splitlines() if line.strip()
        ]
        return {
            "is_git": True,
            "branch": branch,
            "dirty_count": len(changed_files),
            "changed_files": changed_files,
        }

    def list_skills(self) -> list[dict[str, Any]]:
        """Return skill metadata list."""
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "source": skill.source,
                "argument_hint": skill.argument_hint,
            }
            for skill in self.skill_registry.list()
        ]

    def get_skill(self, name: str) -> dict[str, Any] | None:
        """Return skill content preview."""
        skill = self.skill_registry.get(name)
        if skill is None:
            return None
        return {
            "name": skill.name,
            "description": skill.description,
            "source": skill.source,
            "content": skill.prompt,
        }

    def resolve_slash(self, text: str) -> tuple[str | None, str, str]:
        """Resolve a slash command. Returns (expanded_prompt, command, args).

        If not a slash command, returns (None, "", "").
        If slash but unknown skill, returns ("", command, args).
        """
        skill, command, arguments = self.skill_registry.resolve(text)
        if not command:
            return None, "", ""
        if skill is None:
            return "", command, arguments
        return skill.render(arguments), command, arguments

    def switch_model(self, model_spec: str, model_factory: Callable[..., Any]) -> None:
        """Replace the active model client."""
        if self.active_run_id is not None or self._context_operation_lock.locked():
            raise ContextBusyError("context operation is active")
        reasoning_mode = self._resolve_reasoning_mode(model_spec, self.reasoning_mode)
        replacement = _build_model(model_factory, model_spec, reasoning_mode)
        policy = self.model_capabilities.resolve(model_spec)
        if policy is None:
            policy = self.model_capabilities.resolve(replacement)
        controller = (
            self._build_context_controller(replacement, policy)
            if policy is not None
            else None
        )
        self.model = replacement
        self.model_factory = model_factory
        self.model_spec = model_spec
        self.reasoning_mode = reasoning_mode
        self.context_policy = policy
        self.context_controller = controller
        self._save_session()

    def switch_reasoning(self, mode: str) -> None:
        """Replace the active client with one using a declared reasoning mode."""
        if self.active_run_id is not None or self._context_operation_lock.locked():
            raise ContextBusyError("context operation is active")
        if mode != "off" and mode not in self._reasoning_modes_for(self.model_spec):
            raise ValueError(f"unsupported reasoning mode: {mode}")
        if mode == self.reasoning_mode:
            return
        self.model = self._current_model_factory(reasoning_mode=mode)
        self.reasoning_mode = mode
        self._save_session()

    def context_snapshot(self) -> dict[str, Any]:
        """Return a bounded, read-only context-control status payload."""
        state = self.session.get("context_state", {})
        latest = self.compaction_store.load_latest_attempt(self.session_id)
        latest_attempt: dict[str, Any] | None = None
        stale_started = False
        if latest is not None:
            updated_at = str(latest.get("updated_at", ""))
            if latest.get("status") == "started":
                try:
                    updated = datetime.fromisoformat(updated_at)
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=UTC)
                    stale_started = (datetime.now(UTC) - updated).total_seconds() >= 300
                except ValueError:
                    stale_started = True
            latest_attempt = {
                "compaction_id": str(latest.get("compaction_id", "")),
                "status": str(latest.get("status", "")),
                "trigger": str(latest.get("trigger", "")),
                "updated_at": updated_at,
                "stale": stale_started,
            }
        common: dict[str, Any] = {
            "configured": self.context_controller is not None and self.context_policy is not None,
            "model_id": self.model_spec or None,
            "active_run_id": self.active_run_id,
            "context_operation_active": self._context_operation_lock.locked(),
            "checkpoint_id": str(state.get("checkpoint_id", "")) or None,
            "resume_status": str(state.get("resume_status", "canonical_fallback")),
            "checkpoint_resume_enabled": self.compaction_store.checkpoint_resume_enabled,
            "latest_attempt": latest_attempt,
            "stale_started": stale_started,
        }
        if self.context_controller is None or self.context_policy is None:
            return {
                **common,
                "model_limit_tokens": None,
                "output_reserve_tokens": None,
                "effective_limit_tokens": None,
                "used_tokens": None,
                "usage_ratio": None,
                "level": "unconfigured",
                "estimated": None,
                "compactable": False,
            }
        usage = self.context_controller.before_model_request(
            deepcopy(self._active_projection), user_message=""
        ).usage
        return {
            **common,
            "model_limit_tokens": self.context_policy.context_window_tokens,
            "output_reserve_tokens": self.context_policy.output_reserve_tokens,
            "effective_limit_tokens": usage.effective_limit_tokens,
            "used_tokens": usage.used_tokens,
            "usage_ratio": usage.usage_ratio,
            "level": usage.level,
            "estimated": usage.estimated,
            "compactable": self.active_run_id is None
            and not self._context_operation_lock.locked(),
        }

    def set_permission_mode(self, mode: PermissionMode) -> None:
        """Switch the active permission mode at runtime."""
        self.permission_mode = mode
        self.permission_checker = self._permission_checker()
        self._save_session()

    def enter_plan_mode(self, topic: str, path: str | None = None) -> str:
        """Switch to plan mode."""
        plan_path = self.plan_mode.enter(topic, path=path)
        self.runtime_mode = "plan"
        self.session["runtime_mode"] = self.plan_mode.to_dict()
        self.permission_checker = self._permission_checker()
        self._save_session()
        self.session_event_bus.emit("runtime_mode_changed", self.plan_mode.to_dict())
        return plan_path

    def exit_plan_mode(self) -> None:
        """Switch back to default mode."""
        self.plan_mode.exit()
        self.runtime_mode = "default"
        self.session["runtime_mode"] = self.plan_mode.to_dict()
        self.permission_checker = self._permission_checker()
        self._save_session()
        self.session_event_bus.emit("runtime_mode_changed", self.plan_mode.to_dict())

    def request_plan_exit(self) -> dict[str, str]:
        """Submit the current plan for user review instead of exiting directly.

        Reads the plan file and submits a review entry. The turn continues to
        stream; the ``plan_ready_for_review`` event is surfaced to the client by
        ``run_turn``. The actual mode switch happens later via ``approve_plan``.

        Raises ValueError if not currently in plan mode.
        """
        if self.runtime_mode != "plan":
            raise ValueError("not in plan mode")
        plan_path = self.plan_mode.plan_path
        summary = self._plan_summary(plan_path)
        entry = self.plan_review_manager.submit(plan_path, summary)
        return {"review_id": entry.review_id, "plan_path": plan_path, "summary": summary}

    def approve_plan(self) -> bool:
        """Approve the pending plan review and exit plan mode."""
        entry = self.plan_review_manager.resolve("approved")
        if entry is None:
            return False
        self.exit_plan_mode()
        return True

    def reject_plan(self) -> bool:
        """Reject the pending plan review, staying in plan mode.

        Returns True when a review was pending and rejected, False otherwise.
        """
        entry = self.plan_review_manager.resolve("rejected")
        return entry is not None

    def _plan_summary(self, plan_path: str) -> str:
        """Return the first 2000 characters of the plan file (best-effort)."""
        if not plan_path:
            return ""
        target = self.workspace.path(plan_path)
        if not target.is_file():
            return ""
        content = target.read_text(encoding="utf-8", errors="replace")
        return content[:2000]

    def _render_context_for_count(
        self, history: list[dict[str, Any]], user_message: str
    ) -> str:
        prompt, _ = self.context_manager.build(
            user_message=user_message,
            history=history,
            tools=[
                f"{tool.name}: {tool.description} schema={tool.schema}"
                for tool in self.tools.values()
            ],
            workspace_reminders=self._workspace_reminders(),
        )
        return prompt

    def _build_context_controller(
        self, model: Any, policy: ContextPolicy
    ) -> ContextController:
        counter = TokenCounter(model)
        compactor = CompactManager(
            summarizer=StructuredSummarizer(model),
            policy=policy,
            counter=counter,
            checkpoint_verifier=self.compaction_store.verify_checkpoint,
        )
        return ContextController(
            session_id=self.session_id,
            policy=policy,
            counter=counter,
            projector=ContextProjector(),
            compactor=compactor,
            renderer=self._render_context_for_count,
            history_provider=lambda: deepcopy(self.session["history"]),
            active_run_id=lambda: self.active_run_id,
            lifecycle_sink=self._context_lifecycle_sink,
        )

    def _backfill_transcript(self) -> None:
        persisted = self.transcript_store.read_all()
        if persisted:
            self.session["history"] = [self._transcript_item_to_history(item) for item in persisted]
            return
        legacy = deepcopy(list(self.session.get("history", [])))
        batch: list[TranscriptItem] = []
        for index, item in enumerate(legacy):
            enriched = deepcopy(item)
            if not enriched.get("message_id"):
                identity = json.dumps(
                    item, ensure_ascii=False, sort_keys=True, default=str
                )
                enriched["message_id"] = "legacy_" + uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"sage:{self.session_id}:{index}:{identity}",
                ).hex
            enriched.setdefault("run_id", "")
            enriched.setdefault("turn_id", "")
            enriched.setdefault("created_at", now())
            batch.append(self._history_to_transcript_item(enriched))
        if batch:
            self.transcript_store.append_many(batch)
        persisted = self.transcript_store.read_all()
        self.session["history"] = [
            self._transcript_item_to_history(item) for item in persisted
        ]

    def _restore_context_state(self) -> None:
        canonical = deepcopy(self.session["history"])
        self._active_checkpoint = None
        self._active_projection = canonical
        state = self.session.setdefault("context_state", {})
        state["checkpoint_resume_enabled"] = self.compaction_store.checkpoint_resume_enabled
        if not self.compaction_store.checkpoint_resume_enabled:
            state["resume_status"] = "disabled_missing_anchor_key"
            state["active_projection"] = deepcopy(canonical)
            state["checkpoint_id"] = ""
            return
        checkpoint = self.compaction_store.load_latest_checkpoint(self.session_id)
        if checkpoint is None or not self._checkpoint_matches_transcript(
            checkpoint, canonical
        ):
            state["resume_status"] = "canonical_fallback"
            state["active_projection"] = deepcopy(canonical)
            state["checkpoint_id"] = ""
            return
        tail = [
            item
            for item in canonical
            if isinstance(item.get("sequence"), int)
            and int(item["sequence"]) > checkpoint.transcript_end
        ]
        summary = {
            "role": "system",
            "kind": "compact_summary",
            "content": checkpoint.summary.render_for_prompt(),
            "created_at": now(),
        }
        self._active_checkpoint = checkpoint
        self._active_projection = [summary, *tail]
        state["resume_status"] = "checkpoint_restored"
        state["active_projection"] = deepcopy(self._active_projection)
        state["checkpoint_id"] = checkpoint.compaction_id

    def _checkpoint_matches_transcript(
        self,
        checkpoint: CompactionCheckpoint,
        canonical: list[dict[str, Any]],
    ) -> bool:
        try:
            if checkpoint.transcript_start < 1 or checkpoint.transcript_end < checkpoint.transcript_start:
                return False
            sequences = [item.get("sequence") for item in canonical]
            if not sequences:
                return False
            numeric: list[int] = []
            for value in sequences:
                if not isinstance(value, int) or isinstance(value, bool):
                    return False
                numeric.append(value)
            if numeric != list(range(1, len(numeric) + 1)):
                return False
            if checkpoint.transcript_end > numeric[-1]:
                return False
            evidence = self.transcript_store.read_range(
                checkpoint.transcript_start, checkpoint.transcript_end
            )
            if [item.sequence for item in evidence] != list(
                range(checkpoint.transcript_start, checkpoint.transcript_end + 1)
            ):
                return False
            evidence_history = [
                self._transcript_item_to_history(item) for item in evidence
            ]
            if CompactManager._evidence_hash(evidence_history) != checkpoint.evidence_hash:
                return False
            prefix: list[dict[str, Any]] = []
            for item in canonical:
                if item.get("role") == "user":
                    break
                if item.get("role") == "system" and item.get("kind") != "compact_summary":
                    prefix.append(item)
            return CompactManager._prefix_hash(prefix) == checkpoint.prefix_hash
        except Exception:
            return False

    def _append_canonical_item(
        self,
        item: dict[str, Any],
        *,
        append_active: bool = True,
    ) -> dict[str, Any]:
        enriched = deepcopy(item)
        enriched.setdefault("message_id", f"msg_{uuid.uuid4().hex}")
        enriched.setdefault("run_id", self.active_run_id or "")
        enriched.setdefault("turn_id", self._turn_id)
        enriched.setdefault("created_at", now())
        transcript_item = self._history_to_transcript_item(enriched)
        _, sequence = self.transcript_store.append_and_get_sequence(transcript_item)
        enriched["sequence"] = sequence
        self.session["history"].append(deepcopy(enriched))
        if append_active:
            self._active_projection.append(deepcopy(enriched))
        return enriched

    def append_harness_message(self, *, role: str, content: str, run_id: str) -> dict[str, Any]:
        """Persist one public Harness message into the existing session transcript."""
        if role not in {"user", "assistant"}:
            raise ValueError("harness messages must be user or assistant")
        text = content.strip()
        if not text:
            raise ValueError("harness message content must not be empty")
        item = self._append_canonical_item(
            {"role": role, "content": text, "run_id": run_id},
        )
        self._save_session()
        return item

    @staticmethod
    def _history_to_transcript_item(enriched: dict[str, Any]) -> TranscriptItem:
        return TranscriptItem(
            message_id=str(enriched["message_id"]),
            role=str(enriched.get("role", "")),
            content=str(enriched.get("content", "")),
            run_id=str(enriched.get("run_id", "")),
            turn_id=str(enriched.get("turn_id", "")),
            call_id=str(enriched.get("call_id", "")),
            artifact_ref=str(enriched.get("artifact_ref", "")),
            created_at=str(enriched.get("created_at", "")),
            name=str(enriched.get("name", "")),
            args=enriched.get("args", {}),
            is_error=bool(enriched.get("is_error", False)),
            policy_reason=str(enriched.get("policy_reason") or ""),
            security_event_type=str(enriched.get("security_event_type") or ""),
        )

    @staticmethod
    def _transcript_item_to_history(item: TranscriptItem) -> dict[str, Any]:
        result: dict[str, Any] = {
            "message_id": item.message_id,
            "sequence": item.sequence,
            "role": item.role,
            "content": item.content,
            "run_id": item.run_id,
            "turn_id": item.turn_id,
            "created_at": item.created_at,
        }
        optional = {
            "call_id": item.call_id,
            "artifact_ref": item.artifact_ref,
            "name": item.name,
            "policy_reason": item.policy_reason,
            "security_event_type": item.security_event_type,
        }
        result.update({key: value for key, value in optional.items() if value})
        if item.role == "tool":
            result["args"] = dict(item.args)
        if item.is_error:
            result["is_error"] = True
        return result

    async def _context_lifecycle_sink(
        self, event: RunEventBase, result: CompactionResult | None
    ) -> None:
        event_dict = event_to_dict(event)
        if isinstance(event, ContextCompactionStartedEvent):
            self.compaction_store.begin(
                self.session_id,
                event.compaction_id,
                {
                    "run_id": event.run_id,
                    "trigger": event.trigger,
                    "before_tokens": event.before_tokens,
                },
            )
        elif isinstance(event, ContextCompactionCompletedEvent):
            if result is None or result.checkpoint is None:
                raise ValueError("completed compaction is missing its result")
            self.compaction_store.complete(
                self.session_id,
                event.compaction_id,
                result,
                evidence={"transcript_range": list(result.checkpoint.summary.source_transcript_range)},
            )
        elif isinstance(event, ContextCompactionFailedEvent):
            if result is None:
                raise ValueError("failed compaction is missing its result")
            self.compaction_store.fail(self.session_id, event.compaction_id, result)
        if event.run_id:
            self.run_store.append_trace(event.run_id, event_dict)
        self.session_event_bus.emit(event.type, event_dict)

    async def manual_compact(self, focus: str = "") -> CompactionResult:
        if self.context_controller is None:
            raise ValueError("context window is not configured")
        await self._acquire_context_operation()
        try:
            if self.active_run_id is not None:
                raise ContextBusyError("active run")
            compaction_id = f"compact-{uuid.uuid4().hex}"
            before = self.context_controller.before_model_request(
                self._active_projection, user_message=focus
            ).usage.used_tokens
            started = ContextCompactionStartedEvent(
                session_id=self.session_id,
                compaction_id=compaction_id,
                trigger="manual",
                before_tokens=before,
            )
            await self._context_lifecycle_sink(started, None)
            try:
                result = await self.context_controller.manual_compact(
                    focus,
                    history=self._active_projection,
                    previous_checkpoint=self._active_checkpoint,
                    transcript_range=self._active_transcript_range(),
                    compaction_id=compaction_id,
                )
            except BaseException as exc:
                failed_result = CompactionResult(
                    applied=False,
                    projected_history=deepcopy(self._active_projection),
                    checkpoint=self._active_checkpoint,
                    before_tokens=before,
                    after_tokens=before,
                    archived_items=0,
                    reason="compaction_failed",
                    compaction_id=compaction_id,
                    trigger="manual",
                    retryable=True,
                )
                failed = ContextCompactionFailedEvent(
                    session_id=self.session_id,
                    compaction_id=compaction_id,
                    reason="compaction_failed",
                    preserved_original=True,
                    retryable=True,
                )
                with suppress(Exception):
                    await self._context_lifecycle_sink(failed, failed_result)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise RuntimeError("manual compaction failed") from None
            terminal: RunEventBase
            if result.applied:
                terminal = ContextCompactionCompletedEvent(
                    session_id=self.session_id,
                    compaction_id=compaction_id,
                    before_tokens=result.before_tokens,
                    after_tokens=result.after_tokens,
                    archived_items=result.archived_items,
                )
            else:
                terminal = ContextCompactionFailedEvent(
                    session_id=self.session_id,
                    compaction_id=compaction_id,
                    reason=result.reason or "compaction_failed",
                    preserved_original=True,
                    retryable=result.retryable,
                )
            await self._context_lifecycle_sink(terminal, result)
            if result.applied:
                self._apply_compaction_result(result)
            return result
        finally:
            self._context_operation_lock.release()

    async def _acquire_context_operation(self) -> None:
        if self._context_operation_lock.locked():
            raise ContextBusyError("context operation is active")
        await self._context_operation_lock.acquire()

    def _active_transcript_range(self) -> tuple[int, int] | None:
        sequences = [
            int(item["sequence"])
            for item in self._active_projection
            if isinstance(item.get("sequence"), int)
        ]
        if not sequences:
            return None
        return sequences[0], sequences[-1]

    def _apply_compaction_result(self, result: CompactionResult) -> None:
        self._active_projection = deepcopy(result.projected_history)
        self._active_checkpoint = result.checkpoint
        state = self.session.setdefault("context_state", {})
        state["active_projection"] = deepcopy(self._active_projection)
        state["checkpoint_id"] = result.compaction_id
        state["resume_status"] = "checkpoint_active"

    def _persist_run_terminal(
        self,
        run_id: str,
        run_start_time: float,
        *,
        forced_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Durably close a run once, including disconnect-only paths."""
        try:
            existing = self.run_store.get_run(run_id)["events"]
        except FileNotFoundError:
            existing = []
        event_types = {str(event.get("type", "")) for event in existing}
        created: list[dict[str, Any]] = []
        status = forced_status or self.run_store.run_status(run_id)
        if status in {"running", "unknown"}:
            status = "cancelled"
        if status == "cancelled" and "cancelled" not in event_types:
            cancelled = event_to_dict(
                CancelledEvent(run_id=run_id, content="Run cancelled before completion")
            )
            self.run_store.append_trace(run_id, cancelled)
            with suppress(Exception):
                self.session_event_bus.emit("cancelled", cancelled)
            created.append(cancelled)
        if "run_finished" not in event_types:
            finished = event_to_dict(
                RunFinishedEvent(
                    run_id=run_id,
                    status=status,
                    duration_ms=int((time.monotonic() - run_start_time) * 1000),
                    tool_steps=self.run_store.run_tool_count(run_id),
                )
            )
            self.run_store.append_trace(run_id, finished)
            with suppress(Exception):
                self.session_event_bus.emit("run_finished", finished)
            created.append(finished)
        if "turn_finished" not in event_types:
            finished_turn = event_to_dict(TurnFinishedEvent(run_id=run_id))
            self.run_store.append_trace(run_id, finished_turn)
            with suppress(Exception):
                self.session_event_bus.emit("turn_finished", finished_turn)
            created.append(finished_turn)
        return created

    def _record_run_failure(
        self, run_id: str, run_start_time: float, message: str
    ) -> list[dict[str, Any]]:
        error_event = event_to_dict(ErrorEvent(run_id=run_id, message=message))
        with suppress(Exception):
            self.run_store.append_trace(run_id, error_event)
        with suppress(Exception):
            self.session_event_bus.emit("error", error_event)
        terminals: list[dict[str, Any]] = []
        with suppress(Exception):
            terminals = self._persist_run_terminal(
                run_id, run_start_time, forced_status="error"
            )
        return [error_event, *terminals]

    async def run_turn(
        self,
        user_message: str,
        skill_prompt: str | None = None,
        surface_context: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one coding turn, persist events, and stream them to caller.

        ``skill_prompt`` is an expanded skill instruction injected into the LLM
        prompt for this turn only; it is never persisted to session history.

        ``surface_context`` is validated by the API boundary and deep-copied for
        this run so caller mutations cannot change the model's resource binding.

        An active-run lease prevents two concurrent turns on the same session:
        if ``active_run_id`` is already set the call short-circuits with an
        ``error`` event. The whole engine loop plus post-run terminal events
        are wrapped in an outer ``try/finally`` so the lease is always released
        (and session state persisted) even when the consumer closes the async
        generator early via ``aclose()`` / a dropped WebSocket (GeneratorExit).
        Yielding is forbidden inside ``finally`` for an async generator, so the
        cleanup block performs only state mutation (no yield).
        """
        # The run and manual compaction share one operation lease. Acquire it
        # before creating run evidence so a busy rejection has no partial run.
        if self.active_run_id is not None:
            yield event_to_dict(
                ErrorEvent(
                    run_id="",
                    message="A run is already in progress for this session",
                )
            )
            return
        if self._context_operation_lock.locked():
            yield event_to_dict(
                ErrorEvent(
                    run_id="",
                    message="A context operation is already in progress for this session",
                )
            )
            return
        await self._context_operation_lock.acquire()

        self.stop_requested = False
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        frozen_surface_context = deepcopy(dict(surface_context)) if surface_context else None
        self._turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        self.active_run_id = run_id
        run_start_time = time.monotonic()
        prev_mode = self.runtime_mode
        last_notified_review_id = ""
        prepared_events: list[dict[str, Any]] = []
        current_message_id = f"msg_{uuid.uuid4().hex}"
        try:
            self.run_store.start_run(run_id, session_id=self.session_id)
            self.diff_tracker.snapshot_before_run()
            self.memory_manager.build_working_memory(
                self.session, self.runtime_mode, self.permission_mode
            )
            memory_block = self.memory_manager.get_context_block()
            started = event_to_dict(TurnStartedEvent(run_id=run_id))
            self.run_store.append_trace(run_id, started)
            self.session_event_bus.emit("turn_started", started)
            if self.context_controller is not None:
                prepared = await self.context_controller.on_turn_start(
                    deepcopy(self._active_projection),
                    user_message,
                    run_id,
                    previous_checkpoint=self._active_checkpoint,
                    transcript_range=self._active_transcript_range(),
                )
                if prepared.compaction_result is not None and prepared.compaction_result.applied:
                    self._apply_compaction_result(prepared.compaction_result)
                prepared_events = [event_to_dict(event) for event in prepared.events]
            self._append_canonical_item(
                {
                    "message_id": current_message_id,
                    "role": "user",
                    "content": user_message,
                    "created_at": now(),
                }
            )
        except asyncio.CancelledError:
            with suppress(Exception):
                self._persist_run_terminal(
                    run_id, run_start_time, forced_status="cancelled"
                )
            self.active_run_id = None
            self.stop_requested = False
            self._context_operation_lock.release()
            self._save_session()
            raise
        except Exception:
            failure_events = self._record_run_failure(
                run_id, run_start_time, "Run preparation failed"
            )
            self.active_run_id = None
            self.stop_requested = False
            self._context_operation_lock.release()
            self._save_session()
            for event in failure_events:
                yield event
            return

        def before_model_request(history: list[dict[str, Any]]) -> PreparedContext:
            assert self.context_controller is not None
            return self.context_controller.before_model_request(
                history,
                user_message=user_message,
                run_id=run_id,
                current_message_id=current_message_id,
            )

        try:
            engine = Engine(
                model=self.model,
                workspace=self.workspace,
                tools=self.tools,
                context_manager=self.context_manager,
                permission_checker=self.permission_checker,
                policy_checker=self.policy_checker,
                session_id=self.session_id,
                approval_manager=self.approval_manager,
                should_stop=lambda: self.stop_requested,
                history=self._active_projection,
                activated_tools=self.activated_tools,
                run_id=run_id,
                workspace_reminders=self._workspace_reminders(),
                max_steps=50,
                append_user=False,
                current_message_id=current_message_id,
                append_history=self._append_canonical_item,
                before_model_request=(before_model_request if self.context_controller else None),
                model_usage_sink=self._record_model_usage,
            )
            engine_stream = engine.run_turn(
                user_message,
                skill_prompt=skill_prompt,
                memory_block=memory_block,
                surface_context=frozen_surface_context,
            )
        except Exception:
            failure_events = self._record_run_failure(
                run_id, run_start_time, "Run preparation failed"
            )
            self.active_run_id = None
            self.stop_requested = False
            self._context_operation_lock.release()
            self._save_session()
            for event in failure_events:
                yield event
            return
        try:
            for context_event in prepared_events:
                if context_event["type"] not in {
                    "context_compaction_started",
                    "context_compaction_completed",
                    "context_compaction_failed",
                }:
                    self.run_store.append_trace(run_id, context_event)
                    self.session_event_bus.emit(context_event["type"], context_event)
                yield context_event
            # --- Engine loop ---
            try:
                async for event in engine_stream:
                    event = {"run_id": run_id, **event}
                    # Skip persisting ephemeral text_delta events to avoid trace bloat;
                    # they are streamed to the client but not stored in the run trace.
                    if event["type"] != "text_delta":
                        self.run_store.append_trace(run_id, event)
                    self.session_event_bus.emit(event["type"], event)
                    self._sync_session_state()
                    yield event
                    # Surface runtime mode changes (enter/exit plan mode) that happen
                    # during tool execution to the WebSocket stream. These mutations are
                    # performed on the runtime via the tool context, so they are not
                    # part of the engine's own event stream and must be re-injected.
                    if self.runtime_mode != prev_mode:
                        mode_event = event_to_dict(
                            RuntimeModeChangedEvent(
                                run_id=run_id,
                                mode=self.runtime_mode,
                                topic=self.plan_mode.topic,
                                plan_path=self.plan_mode.plan_path,
                            )
                        )
                        mode_event = {"run_id": run_id, **mode_event}
                        self.run_store.append_trace(run_id, mode_event)
                        yield mode_event
                        prev_mode = self.runtime_mode
                    # Surface a pending plan review exactly once per outstanding request.
                    # The exit_plan_mode tool submits a review instead of exiting; the
                    # actual mode switch happens when the user approves via the REST API.
                    pending = self.plan_review_manager.pending
                    if (
                        pending is not None
                        and not pending.event.is_set()
                        and pending.review_id != last_notified_review_id
                    ):
                        review_event = event_to_dict(
                            PlanReadyForReviewEvent(
                                run_id=run_id,
                                review_id=pending.review_id,
                                plan_path=pending.plan_path,
                                summary=pending.summary,
                            )
                        )
                        review_event = {"run_id": run_id, **review_event}
                        self.run_store.append_trace(run_id, review_event)
                        self.session_event_bus.emit("plan_ready_for_review", review_event)
                        yield review_event
                        last_notified_review_id = pending.review_id
            except Exception:
                self._append_canonical_item(
                    {
                        "role": "assistant",
                        "content": "Model request failed; the run was safely terminated.",
                        "is_error": True,
                        "created_at": now(),
                    }
                )
                error_event = event_to_dict(
                    ErrorEvent(run_id=run_id, message="Model request failed")
                )
                error_event = {"run_id": run_id, **error_event}
                self.run_store.append_trace(run_id, error_event)
                self.session_event_bus.emit("error", error_event)
                yield error_event

            # --- Post-run terminal events ---
            # Always emitted (even after an error) so the client learns the run
            # has ended. These run inside the outer try block (yield is legal
            # here); if the consumer closes the generator mid-yield the outer
            # finally still releases the lease. Produce a bounded workspace diff
            # artifact from before/after snapshots and surface it as a
            # workspace_diff_ready event before run_finished.
            try:
                diff = self.diff_tracker.snapshot_after_run(run_id)
                self.diff_tracker.write_artifact(diff, self.run_store.evidence_root)
                diff_event = event_to_dict(
                    WorkspaceDiffReadyEvent(
                        run_id=run_id,
                        changed_files=[f.path for f in diff.changed_files],
                        file_count=diff.file_count,
                        truncated=diff.truncated,
                    )
                )
                diff_event = {"run_id": run_id, **diff_event}
                self.run_store.append_trace(run_id, diff_event)
                self.session_event_bus.emit("workspace_diff_ready", diff_event)
                yield diff_event
            except Exception as exc:  # diff must never break the run
                error_event = event_to_dict(
                    ErrorEvent(run_id=run_id, message=f"workspace diff failed: {exc}")
                )
                error_event = {"run_id": run_id, **error_event}
                self.run_store.append_trace(run_id, error_event)
                self.session_event_bus.emit("error", error_event)
                yield error_event

            duration_ms = int((time.monotonic() - run_start_time) * 1000)
            status = self.run_store.run_status(run_id)
            tool_steps = self.run_store.run_tool_count(run_id)
            finished = event_to_dict(
                RunFinishedEvent(
                    run_id=run_id,
                    status=status,
                    duration_ms=duration_ms,
                    tool_steps=tool_steps,
                )
            )
            finished = {"run_id": run_id, **finished}
            self.run_store.append_trace(run_id, finished)
            self.session_event_bus.emit("run_finished", finished)
            yield finished

            finished_turn = event_to_dict(TurnFinishedEvent(run_id=run_id))
            finished_turn = {"run_id": run_id, **finished_turn}
            self.run_store.append_trace(run_id, finished_turn)
            self.session_event_bus.emit("turn_finished", finished_turn)
            yield finished_turn
        finally:
            # Cleanup: release the lease and persist session state. This runs
            # whether the turn completed normally, the engine raised, or the
            # consumer closed the generator early (GeneratorExit). Note: no
            # yield is allowed inside finally for an async generator.
            with suppress(Exception):
                await cast(Any, engine_stream).aclose()
            with suppress(Exception):
                self._persist_run_terminal(run_id, run_start_time)
            self.stop_requested = False
            self.active_run_id = None
            self._context_operation_lock.release()
            self._save_session()

    def request_stop(self, run_id: str | None = None) -> bool:
        """Request cancellation for the current or next engine checkpoint.

        If ``run_id`` is provided and does not match the currently active run,
        the request is rejected (returns ``False``) without mutating state.
        This prevents a late stop request from a previous run from polluting a
        newer run on the same session. When ``run_id`` is ``None`` (the
        default) the request is accepted unconditionally for backward
        compatibility.
        """
        if run_id is not None and self.active_run_id != run_id:
            return False
        self.stop_requested = True
        self.approval_manager.cancel_session(self.session_id)
        self.plan_review_manager.cancel()
        self.session_event_bus.emit(
            "stop_requested",
            {"session_id": self.session_id, "run_id": self.active_run_id},
        )
        return True

    def _permission_checker(self) -> PermissionChecker:
        return PermissionChecker(
            permission_mode=self.permission_mode,
            approval_policy=self.approval_policy,
            plan_mode=self.runtime_mode == "plan",
        )

    def _workspace_reminders(self) -> list[str]:
        reminders: list[str] = []
        for name in (".sage/SAGE.md", "SAGE.md", "AGENTS.md"):
            path = self.workspace.root / name
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                continue
            reminders.append(f"{name}:\n{content[:12000]}")
        return reminders

    def _sync_session_state(self) -> None:
        self.session["updated_at"] = now()
        self.session["todos"] = self.todo_ledger.to_dict()
        self.session["runtime_mode"] = self.plan_mode.to_dict()
        self.session["activated_tools"] = sorted(self.activated_tools)
        self.session["permission_mode"] = self.permission_mode
        self.session["model_spec"] = self.model_spec
        self.session["reasoning_mode"] = self.reasoning_mode
        self.session["runtime_profile"] = self.runtime_profile
        if self.owner_user_id is not None:
            self.session["owner_user_id"] = self.owner_user_id

    def bind_owner(self, owner_user_id: str) -> None:
        """Persist an owner when an authenticated account adopts a local session."""
        normalized = owner_user_id.strip()
        if not normalized:
            raise ValueError("coding session owner is required")
        if self.owner_user_id is not None and self.owner_user_id != normalized:
            raise ValueError("coding session belongs to another account")
        if self.owner_user_id is None:
            self.owner_user_id = normalized
            self._save_session()

    def _reasoning_modes_for(self, model_spec: str) -> tuple[str, ...]:
        return self.model_reasoning_modes.get(model_spec, ())

    def _resolve_reasoning_mode(self, model_spec: str, mode: str) -> str:
        if mode == "off":
            return "off"
        return mode if mode in self._reasoning_modes_for(model_spec) else "off"

    def _current_model_factory(self, *, reasoning_mode: str | None = None) -> Any:
        return _build_model(
            self.model_factory,
            self.model_spec,
            self.reasoning_mode if reasoning_mode is None else reasoning_mode,
        )

    def _record_model_usage(self, attempt: int, usage: UsageSample) -> None:
        if self.usage_store is None:
            return
        provider, _, _ = self.model_spec.partition(":")
        try:
            self.usage_store.record(
                request_id=f"{self.active_run_id or 'run'}:{attempt}",
                session_id=self.session_id,
                run_id=self.active_run_id or "run",
                provider=provider or "unknown",
                model=self.model_spec or "unknown",
                usage=usage,
            )
        except Exception:
            logger.warning("Unable to persist coding model usage", exc_info=True)

    def _save_session(self) -> None:
        self._sync_session_state()
        self.session_store.save(self.session)

    def _restore_plan_mode(self, runtime_mode: Any) -> None:
        if not isinstance(runtime_mode, dict):
            return
        mode = str(runtime_mode.get("mode", "default"))
        if mode != "plan":
            return
        self.plan_mode.mode = "plan"
        self.plan_mode.topic = str(runtime_mode.get("topic", ""))
        self.plan_mode.plan_path = str(runtime_mode.get("plan_path", ""))


def _build_model(factory: Callable[..., Any], model_spec: str, reasoning_mode: str) -> Any:
    """Call legacy and descriptor-aware model factories without hiding their errors."""
    try:
        factory_signature = signature(factory)
    except (TypeError, ValueError):
        return factory(model_spec, reasoning_mode=reasoning_mode)
    for args, kwargs in (
        ((model_spec,), {"reasoning_mode": reasoning_mode}),
        ((model_spec,), {}),
        ((), {}),
    ):
        try:
            factory_signature.bind(*args, **kwargs)
        except TypeError:
            continue
        return factory(*args, **kwargs)
    return factory()
