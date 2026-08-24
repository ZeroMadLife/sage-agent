"""Recoverable one-stage-at-a-time Learning L3 execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from core.learning.artifact_store import (
    LearningArtifactStore,
    LearningCheckpoint,
    LearningCheckpointConflictError,
    LearningCheckpointStage,
    LearningResumeNotFoundError,
    LearningResumeSummary,
)
from core.learning.materials import LearningMapService, LearningPlan, synthesize_research_map
from core.learning.research import LearningResearchOutcome
from core.learning.tasks import LearningTask


class LearningResearchPort(Protocol):
    async def run(
        self,
        *,
        task: LearningTask,
        plan: LearningPlan,
        unit_id: str,
        thread_id: str,
        parent_run_id: str,
        workspace_path: str,
        capability_revision: str,
        allowed_capabilities: frozenset[str],
        evidence_sufficient: bool,
        remaining_token_budget: int,
    ) -> LearningResearchOutcome: ...


@dataclass(frozen=True, slots=True)
class LearningExecutionContext:
    thread_id: str
    parent_run_id: str
    workspace_path: str
    capability_revision: str
    catalog_revision: str
    allowed_capabilities: frozenset[str]
    remaining_token_budget: int


class LearningExecutionService:
    """Advance only the server-selected next durable L3 stage."""

    def __init__(
        self,
        *,
        store: LearningArtifactStore,
        map_service: LearningMapService,
        research_service: LearningResearchPort | None = None,
    ) -> None:
        self.store = store
        self.map_service = map_service
        self.research_service = research_service

    async def advance(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        context: LearningExecutionContext,
        expected_checkpoint_revision: int,
        idempotency_key: str,
    ) -> LearningResumeSummary:
        if expected_checkpoint_revision < 0:
            raise ValueError("expected checkpoint revision must be non-negative")
        if self.store.is_advance_replay(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task.task_id,
            idempotency_key=idempotency_key,
        ):
            return self.resume(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task=task,
                capability_revision=context.capability_revision,
            )
        try:
            checkpoint = self.store.checkpoint(
                owner_id=owner_id, workspace_id=workspace_id, task_id=task.task_id
            )
        except LearningResumeNotFoundError:
            if expected_checkpoint_revision != 0:
                raise LearningCheckpointConflictError(
                    "Learning checkpoint revision changed"
                ) from None
            await self._start(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task=task,
                context=context,
                idempotency_key=idempotency_key,
            )
            return self.resume(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task=task,
                capability_revision=context.capability_revision,
            )
        if checkpoint.checkpoint_revision != expected_checkpoint_revision:
            raise LearningCheckpointConflictError("Learning checkpoint revision changed")
        if checkpoint.stage in {
            "artifact_ready",
            "blocked",
            "user_input_pending",
            "approval_pending",
        }:
            return self.resume(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task=task,
                capability_revision=context.capability_revision,
            )
        lease = self.store.acquire_lease(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task.task_id,
            lease_owner_id=f"advance:{idempotency_key[:180]}",
        )
        plan = self.store.load_plan(
            owner_id=owner_id, workspace_id=workspace_id, task_id=task.task_id
        )
        await self._advance_stage(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task=task,
            plan=plan,
            context=context,
            checkpoint=lease,
            idempotency_key=idempotency_key,
        )
        return self.resume(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task=task,
            capability_revision=context.capability_revision,
        )

    def resume(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        capability_revision: str,
    ) -> LearningResumeSummary:
        return self.store.resume(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task=task,
            capability_revision=capability_revision,
        )

    async def _start(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        context: LearningExecutionContext,
        idempotency_key: str,
    ) -> None:
        outcome = await self.map_service.build(
            task=task,
            parent_run_id=context.parent_run_id,
            capability_revision=context.capability_revision,
            catalog_revision=context.catalog_revision,
        )
        artifact = self.store.save_artifact(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task=task,
            plan=outcome.plan,
            artifact=outcome.artifact,
            citations=outcome.citations,
            idempotency_key=f"knowledge-map-r{task.task_revision}",
            retention="task",
        )
        self.store.begin_execution(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task=task,
            plan=outcome.plan,
            lease_owner_id=f"advance:{idempotency_key[:180]}",
            idempotency_key=idempotency_key,
            artifact_ref=artifact.artifact_ref,
            evidence_count=len(outcome.citations),
            citation_count=len(outcome.citations),
            gap_codes=(outcome.gap_reason,) if outcome.gap_reason else (),
        )

    async def _advance_stage(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        plan: LearningPlan,
        context: LearningExecutionContext,
        checkpoint: LearningCheckpoint,
        idempotency_key: str,
    ) -> None:
        values = await self._next_values(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task=task,
            plan=plan,
            context=context,
            checkpoint=checkpoint,
        )
        self.store.advance_checkpoint(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task.task_id,
            expected_checkpoint_revision=checkpoint.checkpoint_revision,
            lease_owner_id=checkpoint.lease_owner_id,
            fencing_token=checkpoint.fencing_token,
            evidence_count=cast(int, values.get("evidence_count", checkpoint.evidence_count)),
            citation_count=cast(int, values.get("citation_count", checkpoint.citation_count)),
            gap_codes=cast(tuple[str, ...], values.get("gap_codes", checkpoint.gap_codes)),
            blocking_reason=str(values.get("blocking_reason", checkpoint.blocking_reason)),
            artifact_ref=str(values.get("artifact_ref", checkpoint.artifact_ref)),
            stage=cast(LearningCheckpointStage, values["stage"]),
            next_action=str(values["next_action"]),
            idempotency_key=idempotency_key,
        )

    async def _next_values(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        plan: LearningPlan,
        context: LearningExecutionContext,
        checkpoint: LearningCheckpoint,
    ) -> dict[str, object]:
        stage = checkpoint.stage
        if stage == "knowledge_pending":
            artifact = self.store.read_artifact(
                owner_id=owner_id,
                workspace_id=workspace_id,
                artifact_ref=checkpoint.artifact_ref,
            )
            grounded = bool(artifact.citations)
            return {
                "stage": "knowledge_ready" if grounded else "source_gap",
                "next_action": "synthesize" if grounded else "research",
            }
        if stage == "knowledge_ready":
            return {"stage": "synthesize_pending", "next_action": "synthesize"}
        if stage == "source_gap" and checkpoint.next_action == "research":
            if task.source_policy.web == "allowed_when_insufficient":
                return {"stage": "research_pending", "next_action": "research"}
            return {"stage": "synthesize_pending", "next_action": "synthesize_gap"}
        if stage == "research_pending":
            return await self._research(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task=task,
                plan=plan,
                context=context,
            )
        if stage == "research_ready" or (
            stage == "source_gap" and checkpoint.next_action == "synthesize_gap"
        ):
            return {"stage": "synthesize_pending", "next_action": "synthesize"}
        if stage == "synthesize_pending":
            return {"stage": "artifact_ready", "next_action": "review_artifact"}
        return {"stage": "blocked", "next_action": "resolve_blocker"}

    async def _research(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        plan: LearningPlan,
        context: LearningExecutionContext,
    ) -> dict[str, object]:
        if self.research_service is None:
            return _blocked("learning_research_provider_unavailable")
        outcome = await self.research_service.run(
            task=task,
            plan=plan,
            unit_id=plan.units[0].unit_id,
            thread_id=context.thread_id,
            parent_run_id=context.parent_run_id,
            workspace_path=context.workspace_path,
            capability_revision=context.capability_revision,
            allowed_capabilities=context.allowed_capabilities,
            evidence_sufficient=False,
            remaining_token_budget=context.remaining_token_budget,
        )
        if outcome.status == "succeeded":
            artifact_payload, citations = synthesize_research_map(task, plan, outcome.evidence)
            artifact = self.store.save_artifact(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task=task,
                plan=plan,
                artifact=artifact_payload,
                citations=citations,
                idempotency_key=f"research-map-r{task.task_revision}",
                retention="task",
            )
            return {
                "stage": "research_ready",
                "next_action": "synthesize",
                "evidence_count": len(citations),
                "citation_count": len(citations),
                "gap_codes": (),
                "blocking_reason": "",
                "artifact_ref": artifact.artifact_ref,
            }
        if outcome.status == "source_gap":
            return {
                "stage": "source_gap",
                "next_action": "synthesize_gap",
                "blocking_reason": outcome.reason_code,
                "gap_codes": (outcome.reason_code,) if outcome.reason_code else (),
            }
        return _blocked(outcome.reason_code or "learning_research_provider_unavailable")


def _blocked(reason: str) -> dict[str, object]:
    return {
        "stage": "blocked",
        "next_action": "resolve_blocker",
        "blocking_reason": reason,
        "gap_codes": (reason,),
    }


__all__ = ["LearningExecutionContext", "LearningExecutionService"]
