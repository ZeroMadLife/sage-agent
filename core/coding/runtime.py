"""Runtime assembly for a web coding-agent session."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from core.coding.context import IGNORED_PATH_NAMES, ContextManager, WorkspaceContext, now
from core.coding.engine.engine import Engine
from core.coding.engine.events import (
    PlanReadyForReviewEvent,
    RuntimeModeChangedEvent,
    TurnFinishedEvent,
    TurnStartedEvent,
    event_to_dict,
)
from core.coding.multiagent import WorkerManager
from core.coding.persistence import CodingSessionStore, RunStore, SessionEventBus, TodoLedger
from core.coding.plan_mode import PlanModeManager
from core.coding.plan_review import PlanReviewManager
from core.coding.skills import SkillRegistry
from core.coding.tool_executor import (
    ApprovalManager,
    ApprovalPolicy,
    PermissionChecker,
    ToolPolicyChecker,
)
from core.coding.tools.base import ToolContext
from core.coding.tools.registry import build_tool_registry


class CodingRuntime:
    """A complete coding-agent session state."""

    def __init__(
        self,
        session_id: str,
        workspace_root: Path | str,
        model: Any,
        storage_root: Path | str,
        model_factory: Callable[[], Any] | None = None,
        approval_policy: ApprovalPolicy = "auto",
        session_state: dict[str, Any] | None = None,
        save_on_init: bool = True,
        permission_mode: str = "default",
    ) -> None:
        self.session_id = session_id
        self.workspace = WorkspaceContext(root=Path(workspace_root))
        self.model = model
        self.model_factory = model_factory or (lambda: model)
        self.storage_root = Path(storage_root)
        self.session_store = CodingSessionStore(self.storage_root / "sessions")
        self.run_store = RunStore(self.storage_root / "runs")
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
                "todos": {"next_id": 1, "items": []},
            }
        )
        self.session["id"] = session_id
        self.session["workspace_root"] = str(self.workspace.root)
        self.session.setdefault("history", [])
        self.session.setdefault("runtime_mode", {"mode": "default"})
        self.session.setdefault("todos", {"next_id": 1, "items": []})
        self.session.setdefault("activated_tools", [])
        self.activated_tools = {
            str(name) for name in self.session.get("activated_tools", []) if str(name).strip()
        }
        self.todo_ledger = TodoLedger(self.session["todos"])
        self.plan_mode = PlanModeManager(self.workspace.root)
        self._restore_plan_mode(self.session["runtime_mode"])
        self.worker_manager = WorkerManager(self.workspace, self.model_factory)
        self.context_manager = ContextManager()
        self.approval_policy = approval_policy
        self.permission_mode = str(self.session.get("permission_mode", permission_mode))
        self.approval_manager = ApprovalManager()
        self.plan_review_manager = PlanReviewManager()
        self.stop_requested = False
        self.runtime_mode = self.plan_mode.mode
        self.permission_checker = self._permission_checker()
        self.policy_checker = ToolPolicyChecker(self.workspace)
        self.tool_context = ToolContext(
            runtime=self,
            todo_ledger=self.todo_ledger,
            worker_manager=self.worker_manager,
        )
        self.tools = build_tool_registry(
            self.workspace,
            tool_context=self.tool_context,
            activated_tools=self.activated_tools,
        )
        self.skill_registry = SkillRegistry(root=self.workspace.root)
        self.model_spec: str = ""
        if save_on_init:
            self._save_session()

    @classmethod
    def resume(
        cls,
        session_id: str,
        model: Any,
        storage_root: Path | str,
        model_factory: Callable[[], Any] | None = None,
        approval_policy: ApprovalPolicy = "auto",
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
        )

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

    def switch_model(self, model_spec: str, model_factory: Callable[[], Any]) -> None:
        """Replace the active model client."""
        self.model = model_factory()
        self.model_spec = model_spec

    def set_permission_mode(self, mode: str) -> None:
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

    async def run_turn(
        self, user_message: str, skill_prompt: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one coding turn, persist events, and stream them to caller.

        ``skill_prompt`` is an expanded skill instruction injected into the LLM
        prompt for this turn only; it is never persisted to session history.
        """
        self.stop_requested = False
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        self.run_store.start_run(run_id)
        started = event_to_dict(TurnStartedEvent(run_id=run_id))
        self.run_store.append_trace(run_id, started)
        self.session_event_bus.emit("turn_started", started)
        prev_mode = self.runtime_mode
        last_notified_review_id = ""
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
            history=self.session["history"],
            activated_tools=self.activated_tools,
            run_id=run_id,
            workspace_reminders=self._workspace_reminders(),
            max_steps=50,
        )
        async for event in engine.run_turn(user_message, skill_prompt=skill_prompt):
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
        finished = event_to_dict(TurnFinishedEvent(run_id=run_id))
        self.run_store.append_trace(run_id, finished)
        self.session_event_bus.emit("turn_finished", finished)
        self.stop_requested = False
        self._save_session()

    def request_stop(self) -> None:
        """Request cancellation for the current or next engine checkpoint."""
        self.stop_requested = True
        self.approval_manager.cancel_session(self.session_id)
        self.plan_review_manager.cancel()
        self.session_event_bus.emit("stop_requested", {"session_id": self.session_id})

    def _permission_checker(self) -> PermissionChecker:
        return PermissionChecker(
            permission_mode=self.permission_mode,
            approval_policy=self.approval_policy,
            plan_mode=self.runtime_mode == "plan",
        )

    def _workspace_reminders(self) -> list[str]:
        reminders: list[str] = []
        for name in ("SAGE.md", "AGENTS.md"):
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
