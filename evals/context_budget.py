"""Deterministic ablation runner for Sage Context Budget v2."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from sage_harness.config import HarnessRunContext
from sage_harness.middleware.context_compaction import ContextCompactionMiddleware

from core.coding.context import ContextPolicy
from core.coding.persistence.tool_result_store import (
    PERSIST_THRESHOLD_BYTES,
    ToolResultStore,
)

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CASES = _ROOT / "evals" / "context_budget_v2_cases.json"
_DEFAULT_REPORT = _ROOT / "evals" / "reports" / "context_budget_v2_1_2026-07-25.json"
_MARKER = re.compile(r"\[(?:DECISION|PROBE):[^\]]+\]")


@dataclass(frozen=True, slots=True)
class ContextCase:
    case_id: str
    tool_turns: int
    result_bytes: int
    fill: str
    artifact_eligible: bool = True


@dataclass(frozen=True, slots=True)
class Variant:
    variant_id: str
    artifact_offload_threshold_bytes: int | None
    pruning_enabled: bool
    compaction_enabled: bool
    artifact_reload_enabled: bool


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    variant_id: str
    peak_pre_compaction_tokens: int
    peak_model_input_tokens: int
    total_model_input_tokens: int
    checkpoint_content_bytes: int
    pruning_count: int
    compaction_count: int
    compaction_token_usage: int
    exact_user_retained: bool
    surviving_tool_pairs_valid: bool
    decision_marker_retained: bool
    artifact_probe_count: int
    artifact_probe_recovered: int
    duration_ms: float


class DeterministicContextCompactor(ContextCompactionMiddleware):
    """Use the production cutoff/state logic with a stable marker-preserving summary."""

    def __init__(
        self,
        *,
        working_set_tokens: int,
        keep_tokens: int,
        pruning_enabled: bool,
        compaction_enabled: bool,
    ) -> None:
        super().__init__(
            FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
            working_set_tokens=working_set_tokens,
            keep_tokens=keep_tokens,
            summary_input_tokens=32_000,
            static_overhead_tokens=8_000,
        )
        self._pruning_enabled = pruning_enabled
        self._compaction_enabled = compaction_enabled

    def _prune_artifact_backed_tools(self, *args: Any, **kwargs: Any) -> Any:
        if not self._pruning_enabled:
            messages = args[2]
            usage = args[3]
            return messages, usage, {}
        return super()._prune_artifact_backed_tools(*args, **kwargs)

    def _can_attempt(self, state: Mapping[str, Any], before_tokens: int) -> bool:
        return self._compaction_enabled and super()._can_attempt(state, before_tokens)

    @staticmethod
    def _deterministic_summary(
        messages: list[AnyMessage],
        previous_summary: str,
    ) -> tuple[str, int]:
        markers: list[str] = []
        artifact_refs: list[str] = []
        ids: list[str] = []
        for message in messages:
            ids.append(str(message.id or message.type))
            markers.extend(_MARKER.findall(str(message.content)))
            artifact = getattr(message, "artifact", None)
            if isinstance(artifact, dict):
                artifact_ref = artifact.get("artifact_ref")
                if isinstance(artifact_ref, str):
                    artifact_refs.append(artifact_ref)
        prior_markers = _MARKER.findall(previous_summary)
        summary = "\n".join(
            (
                "Deterministic bounded handoff.",
                f"compacted_message_ids={','.join(ids)}",
                f"markers={','.join(dict.fromkeys([*prior_markers, *markers]))}",
                f"artifact_refs={','.join(dict.fromkeys(artifact_refs))}",
            )
        )
        estimated_usage = count_tokens_approximately([*messages, HumanMessage(content=summary)])
        return summary, estimated_usage

    def _generate_summary(
        self,
        messages: list[AnyMessage],
        previous_summary: str,
    ) -> tuple[str, int]:
        return self._deterministic_summary(messages, previous_summary)

    async def _agenerate_summary(
        self,
        messages: list[AnyMessage],
        previous_summary: str,
    ) -> tuple[str, int]:
        return self._deterministic_summary(messages, previous_summary)


VARIANTS = (
    Variant("A0_previous_offload", PERSIST_THRESHOLD_BYTES, False, False, False),
    Variant("A1_offload_4k", 4 * 1_024, False, False, False),
    Variant("A2_recoverable_prune", PERSIST_THRESHOLD_BYTES, True, False, False),
    Variant("A3_semantic_compact", PERSIST_THRESHOLD_BYTES, True, True, False),
    Variant("A4_full", PERSIST_THRESHOLD_BYTES, True, True, True),
)


def load_manifest(path: Path = _DEFAULT_CASES) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("cases"), list)
    ):
        raise ValueError("invalid Context Budget v2 manifest")
    return {str(key): value for key, value in payload.items()}


def _content_bytes(case: ContextCase, turn: int) -> tuple[str, str, str]:
    decision = f"[DECISION:{case.case_id}:retain]" if turn == 0 else ""
    probe = f"[PROBE:{case.case_id}:{turn}]"
    header = f"case={case.case_id};turn={turn};{decision}\n"
    fill_unit = "知识证据" if case.fill == "unicode" else "context-evidence-"
    suffix = "\nend-of-result"
    fixed = len((header + probe + suffix).encode("utf-8"))
    target_fill = max(case.result_bytes - fixed, 0)
    repeated = (fill_unit * (target_fill // len(fill_unit.encode("utf-8")) + 2)).encode("utf-8")
    split = target_fill // 2
    before = repeated[:split].decode("utf-8", errors="ignore")
    after = repeated[split:target_fill].decode("utf-8", errors="ignore")
    return header + before + probe + after + suffix, decision, probe


def _checkpoint_content_bytes(messages: list[AnyMessage]) -> int:
    return sum(len(str(message.content).encode("utf-8")) for message in messages)


def _tool_pairs_valid(messages: list[AnyMessage]) -> bool:
    ai_call_ids = {
        str(call.get("id"))
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call.get("id")
    }
    return all(
        not isinstance(message, ToolMessage) or message.tool_call_id in ai_call_ids
        for message in messages
    )


async def run_case(
    case: ContextCase,
    variant: Variant,
    *,
    working_set_tokens: int = 64_000,
    keep_tokens: int = 24_000,
) -> CaseResult:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="sage-context-eval-") as temp_dir:
        root = Path(temp_dir)
        session_id = f"session-{case.case_id}"
        run_id = "run-context-eval"
        store = ToolResultStore(root, session_id, run_id)
        user_id = f"harness:{run_id}:user"
        user_message = HumanMessage(
            content=f"Complete {case.case_id}; retain all explicit decisions.",
            id=user_id,
        )
        messages: list[AnyMessage] = [user_message]
        state: dict[str, Any] = {
            "messages": messages,
            "budget_run_id": run_id,
            "run_token_usage": 0,
            "run_token_limit": 250_000,
            "context_compaction_run_id": run_id,
            "context_compaction_count": 0,
            "context_compaction_token_usage": 0,
            "context_compaction_failure_count": 0,
        }
        runtime = SimpleNamespace(
            context=HarnessRunContext(
                thread_id=session_id,
                run_id=run_id,
                owner_id="eval",
                workspace_id="context-eval",
                workspace_path=str(root),
            )
        )
        compactor = DeterministicContextCompactor(
            working_set_tokens=working_set_tokens,
            keep_tokens=keep_tokens,
            pruning_enabled=variant.pruning_enabled,
            compaction_enabled=variant.compaction_enabled,
        )
        peak_pre = 0
        peak_model = 0
        total_model = 0
        decisions: list[str] = []
        artifacts: list[tuple[str, str]] = []

        for turn in range(case.tool_turns):
            call_id = f"call-{turn}"
            consumed = (
                f"Processed tool turn {turn - 1}. "
                f"[DECISION:{case.case_id}:retain]"
                if turn == 1
                else f"Processed tool turn {turn - 1}."
                if turn > 1
                else ""
            )
            messages.append(
                AIMessage(
                    content=consumed,
                    id=f"ai-{turn}",
                    tool_calls=[
                        {
                            "name": "synthetic_tool",
                            "args": {"turn": turn},
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                )
            )
            full_content, decision, probe = _content_bytes(case, turn)
            if decision:
                decisions.append(decision)
            if (
                variant.artifact_offload_threshold_bytes is not None
                and case.artifact_eligible
                and len(full_content.encode("utf-8"))
                >= variant.artifact_offload_threshold_bytes
            ):
                receipt = store.archive(call_id, full_content)
                tool_message = ToolMessage(
                    content=receipt.preview,
                    name="synthetic_tool",
                    tool_call_id=call_id,
                    id=f"tool-{turn}",
                    artifact={
                        "artifact_ref": receipt.artifact_ref,
                        "original_chars": receipt.original_chars,
                        "truncated": receipt.truncated,
                    },
                )
                artifacts.append((receipt.artifact_ref, probe))
            else:
                tool_message = ToolMessage(
                    content=full_content,
                    name="synthetic_tool",
                    tool_call_id=call_id,
                    id=f"tool-{turn}",
                )
            messages.append(tool_message)
            state["messages"] = messages
            pre_tokens = count_tokens_approximately(messages) + 8_000
            peak_pre = max(peak_pre, pre_tokens)
            if variant.pruning_enabled or variant.compaction_enabled:
                update = await compactor.abefore_model(state, runtime)  # type: ignore[arg-type]
                if update:
                    replacement = update.get("messages")
                    if isinstance(replacement, list):
                        messages = list(replacement[1:])
                        update = {key: value for key, value in update.items() if key != "messages"}
                    state.update(update)
                    state["messages"] = messages
            model_tokens = count_tokens_approximately(messages) + 8_000
            peak_model = max(peak_model, model_tokens)
            total_model += model_tokens

        rendered = "\n".join(str(message.content) for message in messages)
        summary = str(state.get("summary_text") or "")
        recovered = 0
        if variant.artifact_reload_enabled:
            for artifact_ref, probe in artifacts:
                cursor = store.read_session_slice(
                    artifact_ref,
                    offset_bytes=0,
                    max_bytes=16 * 1024,
                )
                chunks = [cursor.content]
                while cursor.truncated:
                    cursor = store.read_session_slice(
                        artifact_ref,
                        offset_bytes=cursor.next_offset_bytes,
                        max_bytes=16 * 1024,
                    )
                    chunks.append(cursor.content)
                recovered += int(probe in "".join(chunks))

        return CaseResult(
            case_id=case.case_id,
            variant_id=variant.variant_id,
            peak_pre_compaction_tokens=peak_pre,
            peak_model_input_tokens=peak_model,
            total_model_input_tokens=total_model,
            checkpoint_content_bytes=_checkpoint_content_bytes(messages),
            pruning_count=int(state.get("context_pruning_count", 0)),
            compaction_count=int(state.get("context_compaction_count", 0)),
            compaction_token_usage=int(state.get("context_compaction_token_usage", 0)),
            exact_user_retained=any(message.id == user_id for message in messages),
            surviving_tool_pairs_valid=_tool_pairs_valid(messages),
            decision_marker_retained=all(
                marker in rendered or marker in summary for marker in decisions
            ),
            artifact_probe_count=len(artifacts),
            artifact_probe_recovered=recovered,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
        )


def _aggregate(
    results: list[CaseResult],
    baseline_total: int,
    baseline_checkpoint_bytes: int,
) -> dict[str, Any]:
    total_input = sum(item.total_model_input_tokens for item in results)
    checkpoint_bytes = sum(item.checkpoint_content_bytes for item in results)
    compaction_tokens = sum(item.compaction_token_usage for item in results)
    token_reduction = 1.0 - total_input / baseline_total if baseline_total else 0.0
    overhead_ratio = compaction_tokens / baseline_total if baseline_total else 0.0
    artifact_count = sum(item.artifact_probe_count for item in results)
    artifact_recovered = sum(item.artifact_probe_recovered for item in results)
    return {
        "case_count": len(results),
        "peak_model_input_tokens": max(item.peak_model_input_tokens for item in results),
        "total_model_input_tokens": total_input,
        "token_reduction_vs_a0": round(token_reduction, 6),
        "checkpoint_content_bytes": checkpoint_bytes,
        "checkpoint_reduction_vs_a0": round(
            1.0 - checkpoint_bytes / baseline_checkpoint_bytes
            if baseline_checkpoint_bytes
            else 0.0,
            6,
        ),
        "pruning_count": sum(item.pruning_count for item in results),
        "compaction_count": sum(item.compaction_count for item in results),
        "compaction_token_usage": compaction_tokens,
        "compaction_overhead_ratio_vs_a0": round(overhead_ratio, 6),
        "net_token_reduction_after_compaction_cost": round(
            token_reduction - overhead_ratio,
            6,
        ),
        "exact_user_retention_rate": sum(item.exact_user_retained for item in results)
        / len(results),
        "surviving_tool_pair_valid_rate": sum(item.surviving_tool_pairs_valid for item in results)
        / len(results),
        "decision_marker_retention_rate": sum(item.decision_marker_retained for item in results)
        / len(results),
        "artifact_probe_recovery_rate": (
            artifact_recovered / artifact_count if artifact_count else None
        ),
        "duration_ms": round(sum(item.duration_ms for item in results), 3),
    }


async def run_evaluation(manifest_path: Path = _DEFAULT_CASES) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    cases = [ContextCase(**item) for item in manifest["cases"]]
    initial_by_variant: dict[str, list[CaseResult]] = {}
    for variant in VARIANTS:
        initial_by_variant[variant.variant_id] = [await run_case(case, variant) for case in cases]
    baseline_total = sum(
        item.total_model_input_tokens for item in initial_by_variant["A0_previous_offload"]
    )
    baseline_checkpoint_bytes = sum(
        item.checkpoint_content_bytes for item in initial_by_variant["A0_previous_offload"]
    )
    pre_optimization_ablation = {
        variant.variant_id: _aggregate(
            initial_by_variant[variant.variant_id],
            baseline_total,
            baseline_checkpoint_bytes,
        )
        for variant in VARIANTS
    }

    sweep: list[dict[str, Any]] = []
    for candidate in manifest["threshold_candidates"]:
        results = [
            await run_case(
                case,
                VARIANTS[-1],
                working_set_tokens=int(candidate["working_set_tokens"]),
                keep_tokens=int(candidate["keep_tokens"]),
            )
            for case in cases
        ]
        aggregate = _aggregate(results, baseline_total, baseline_checkpoint_bytes)
        quality_ok = all(
            aggregate[key] == 1.0
            for key in (
                "exact_user_retention_rate",
                "surviving_tool_pair_valid_rate",
                "decision_marker_retention_rate",
                "artifact_probe_recovery_rate",
            )
        )
        overhead_ratio = aggregate["compaction_token_usage"] / baseline_total
        score = aggregate["token_reduction_vs_a0"] - overhead_ratio if quality_ok else -1.0
        sweep.append(
            {
                **candidate,
                **aggregate,
                "quality_gate_passed": quality_ok,
                "selection_score": round(score, 6),
            }
        )
    recommended = max(sweep, key=lambda item: item["selection_score"])
    optimized_by_variant: dict[str, list[CaseResult]] = {}
    for variant in VARIANTS:
        optimized_by_variant[variant.variant_id] = [
            await run_case(
                case,
                variant,
                working_set_tokens=int(recommended["working_set_tokens"]),
                keep_tokens=int(recommended["keep_tokens"]),
            )
            for case in cases
        ]
    ablation = {
        variant.variant_id: _aggregate(
            optimized_by_variant[variant.variant_id],
            baseline_total,
            baseline_checkpoint_bytes,
        )
        for variant in VARIANTS
    }
    policy = ContextPolicy(context_window_tokens=1_000_000, output_reserve_tokens=64_000)
    baseline_compact_tokens = int(policy.effective_limit_tokens * policy.compact_ratio)
    source_commit, dirty = _git_state()
    return {
        "evaluation_id": manifest["evaluation_id"],
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_commit": source_commit,
        "source_dirty": dirty,
        "scope": manifest["scope"],
        "case_count": len(cases),
        "baseline_diagnosis": {
            "hard_window_effective_tokens": policy.effective_limit_tokens,
            "hard_window_compact_tokens": baseline_compact_tokens,
            "run_token_limit": 250_000,
            "compact_reachable_before_run_cap": baseline_compact_tokens < 250_000,
            "artifact_offload_threshold_bytes": PERSIST_THRESHOLD_BYTES,
            "rejected_artifact_offload_candidate_bytes": 4 * 1_024,
            "model_visible_artifact_reload": True,
        },
        "pre_optimization_threshold": {
            "working_set_tokens": 64_000,
            "keep_tokens": 24_000,
        },
        "pre_optimization_ablation": pre_optimization_ablation,
        "ablation": ablation,
        "threshold_sweep": sweep,
        "recommended_threshold": {
            "working_set_tokens": recommended["working_set_tokens"],
            "keep_tokens": recommended["keep_tokens"],
            "selection_score": recommended["selection_score"],
        },
        "cases": {
            variant_id: [asdict(item) for item in results]
            for variant_id, results in optimized_by_variant.items()
        },
        "known_gaps": [
            "Deterministic summaries preserve explicit markers; natural-language summary quality is not measured.",
            "Token counts are provider-neutral estimates and must not be presented as provider billing usage.",
            "Latency excludes a live summary-model call and is informational only.",
            "Cheap pruning requires a later textual model receipt; semantic correctness of that receipt needs a live-model benchmark.",
        ],
    }


def _git_state() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    return commit, dirty


def write_report(report: dict[str, Any], output: Path = _DEFAULT_REPORT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = output.with_suffix(".md")
    lines = [
        "# Sage Context Governance v2.1 消融报告",
        "",
        f"- Source commit: `{report['source_commit']}`",
        f"- Cases: {report['case_count']}",
        f"- Scope: {report['scope']}",
        "",
        "| Variant | Total input | Reduction vs A0 | Peak input | Prunes | Compactions | User | Tool pairs | Decision | Artifact |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant_id, metrics in report["ablation"].items():
        artifact = metrics["artifact_probe_recovery_rate"]
        lines.append(
            f"| {variant_id} | {metrics['total_model_input_tokens']} | "
            f"{metrics['token_reduction_vs_a0']:.2%} | {metrics['peak_model_input_tokens']} | "
            f"{metrics['pruning_count']} | {metrics['compaction_count']} | "
            f"{metrics['exact_user_retention_rate']:.0%} | "
            f"{metrics['surviving_tool_pair_valid_rate']:.0%} | "
            f"{metrics['decision_marker_retention_rate']:.0%} | "
            f"{'N/A' if artifact is None else f'{artifact:.0%}'} |"
        )
    recommended = report["recommended_threshold"]
    lines.extend(
        [
            "",
            "## 阈值结论",
            "",
            f"固定扫描推荐 `working_set={recommended['working_set_tokens']}`、"
            f"`keep={recommended['keep_tokens']}`，selection score "
            f"`{recommended['selection_score']}`。",
            "",
            "本报告只证明机制成本与安全不变量，不证明自然语言回答质量。",
        ]
    )
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=_DEFAULT_REPORT)
    args = parser.parse_args()
    report = asyncio.run(run_evaluation(args.manifest))
    write_report(report, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "recommended_threshold": report["recommended_threshold"],
                "ablation": report["ablation"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
