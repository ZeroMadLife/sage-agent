"""Benchmark runner for the Sage coding harness.

Drives 10 deterministic scenarios through the real CodingRuntime + tool stack
using a ScriptedApiClient (no live LLM). Driving the real runtime exercises
the active-run lease, run_finished terminal event, workspace diff artifact,
session evidence, and memory injection paths that a direct Engine construction
would bypass. The benchmark is informational: it emits JSON, Markdown, and a
self-contained HTML report under ``evals/coding/results/`` and never gates the
build.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Make the repository root importable when run as ``python -m evals.coding.runner``.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.core.coding.scripted_api_client import ScriptedApiClient  # noqa: E402

from core.coding.runtime import CodingRuntime  # noqa: E402
from core.coding.tool_executor import PermissionMode  # noqa: E402
from evals.coding.assertions import (  # noqa: E402
    assert_approval_requested,
    assert_diff_ready,
    assert_files_match,
    assert_memory_saved,
    assert_no_write,
    assert_policy_denial,
    assert_run_finished,
    assert_tool_calls_match,
)
from evals.coding.metrics import BenchmarkReport, ScenarioResult  # noqa: E402
from evals.coding.report import generate_html_report  # noqa: E402
from evals.coding.scenarios import SCENARIOS, Scenario  # noqa: E402


def _permission_mode_for(scenario: Scenario) -> PermissionMode:
    """Pick the permission mode that exercises the scenario's category."""
    if scenario.category == "policy_boundary" and "plan" in scenario.name:
        return "plan"
    if scenario.category == "policy_boundary":
        return "default"
    if scenario.category == "controlled_edit":
        return "accept_edits"
    return "auto"


def _build_runtime(
    scenario: Scenario,
    workspace_root: Path,
    storage_root: Path,
    *,
    session_suffix: str = "",
    model_responses: list[str] | None = None,
    permission_mode: PermissionMode | None = None,
) -> CodingRuntime:
    """Assemble a real CodingRuntime wired for one scenario.

    Driving the runtime (rather than constructing an Engine directly) routes
    the turn through the lease, diff tracker, memory manager, session event
    bus, and run_finished/turn_finished terminal events. The runtime exposes
    ``memory_manager`` and ``approval_manager`` to the tool context, so the
    ``remember`` tool and the default-mode approval flow work end to end.
    """
    session_id = f"bench_{scenario.name}{session_suffix}"
    responses = (
        list(model_responses) if model_responses is not None else list(scenario.model_responses)
    )
    return CodingRuntime(
        session_id=session_id,
        workspace_root=workspace_root,
        model=ScriptedApiClient(responses),
        storage_root=storage_root,
        model_factory=lambda: ScriptedApiClient(list(responses)),
        permission_mode=permission_mode or _permission_mode_for(scenario),
        save_on_init=False,
    )


async def _run_turn_with_auto_approval(
    runtime: CodingRuntime, prompt: str, timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Run a turn, granting any pending approval so the flow is non-blocking.

    Used by scenarios that explicitly expect a confirmation boundary, including
    durable memory writes. The engine emits ``approval_required`` and then blocks
    waiting for resolution. We run the turn in a task and, in parallel, resolve
    exactly one pending entry, which models one user confirmation.
    """
    events: list[dict[str, Any]] = []

    async def collect() -> None:
        async for event in runtime.run_turn(prompt):
            events.append(event)

    task = asyncio.create_task(collect())

    async def grant() -> None:
        # Wait until the runtime submits an approval entry, then grant it once.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = (
                runtime.approval_manager.pending(runtime.session_id)
                if runtime.approval_manager
                else None
            )
            if pending is not None and runtime.approval_manager is not None:
                runtime.approval_manager.resolve(
                    runtime.session_id, pending["approval_id"], "once"
                )
                return
            await asyncio.sleep(0.02)
        # No approval surfaced within the window; leave the turn to finish/timeout.

    granters: list[asyncio.Task[None]] = []
    if runtime.approval_manager is not None:
        granters.append(asyncio.create_task(grant()))

    try:
        await asyncio.wait_for(task, timeout=timeout)
    except TimeoutError:
        task.cancel()
    finally:
        for g in granters:
            if not g.done():
                g.cancel()
    return events


async def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Run one benchmark scenario and return its result."""
    workspace_dir = tempfile.mkdtemp(prefix=f"sage_bench_{scenario.name}_")
    workspace_root = Path(workspace_dir)
    try:
        for path, content in scenario.workspace_files.items():
            fpath = workspace_root / path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")

        # Pin durable-memory storage inside the workspace (.coding/) so the
        # memory assertions can find persisted facts and cleanup is automatic.
        storage_root = workspace_root / ".coding"
        runtime = _build_runtime(scenario, workspace_root, storage_root)

        start_time = time.monotonic()
        events: list[dict[str, Any]] = []
        try:
            if scenario.expected_approval:
                events = await _run_turn_with_auto_approval(runtime, scenario.prompt)
            else:
                async for event in runtime.run_turn(scenario.prompt):
                    events.append(event)
        except Exception:  # benchmark must not crash the suite
            pass
        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Run assertions
        passed = True
        detail = ""

        if scenario.expected_no_write and not assert_no_write(events):
            passed = False
            detail = "unexpected write occurred"

        if (
            scenario.expected_files
            and not assert_files_match(workspace_root, scenario.expected_files)
            and not detail
        ):
            passed = False
            detail = "files don't match expected"

        if (
            scenario.expected_tool_calls
            and not assert_tool_calls_match(events, scenario.expected_tool_calls)
            and not detail
        ):
            passed = False
            detail = "tool calls don't match"

        if scenario.expected_denial and not assert_policy_denial(events) and not detail:
            passed = False
            detail = "expected policy denial not found"

        if scenario.expected_approval and not assert_approval_requested(events) and not detail:
            passed = False
            detail = "expected approval not found"

        if (
            scenario.memory_fact
            and not assert_memory_saved(workspace_root, scenario.memory_fact)
            and not detail
        ):
            passed = False
            detail = "memory not saved"

        # Controlled edits drive the real runtime, which always emits
        # run_finished and workspace_diff_ready terminal events. Asserting
        # their presence verifies the lease + diff lifecycle end to end.
        if scenario.category == "controlled_edit":
            if not assert_run_finished(events) and not detail:
                passed = False
                detail = "run_finished event not emitted"
            if not assert_diff_ready(events) and not detail:
                passed = False
                detail = "workspace_diff_ready event not emitted"

        # memory_continuity: run a second session on the same workspace to
        # verify the durable fact written in the first session is recalled
        # into the memory context block of a fresh runtime.
        if scenario.category == "memory_continuity" and scenario.memory_fact and not detail:
            runtime2 = _build_runtime(
                scenario,
                workspace_root,
                storage_root,
                session_suffix="_recall",
                model_responses=["<final>recalled</final>"],
                permission_mode="auto",
            )
            runtime2.memory_manager.build_working_memory(
                runtime2.session, runtime2.runtime_mode, runtime2.permission_mode
            )
            context = runtime2.memory_manager.get_context_block()
            if scenario.memory_fact not in context:
                passed = False
                detail = "memory not recalled in second session"

        # Compute metrics
        tool_calls = sum(1 for e in events if e.get("type") == "tool_call")
        tool_errors = sum(1 for e in events if e.get("type") == "tool_result" and e.get("is_error"))
        # A policy-compliant run either expected and saw a denial, or never
        # triggered an unexpected denial.
        policy_compliant = True
        if scenario.expected_denial:
            policy_compliant = assert_policy_denial(events)
        else:
            policy_compliant = not any(
                e.get("type") == "tool_result"
                and e.get("is_error")
                and any(
                    marker in str(e.get("content", ""))
                    for marker in ("plan_mode", "prior_read_required")
                )
                for e in events
            )

        return ScenarioResult(
            name=scenario.name,
            category=scenario.category,
            passed=passed,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            policy_compliant=policy_compliant,
            duration_ms=duration_ms,
            detail=detail,
        )
    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)


async def run_benchmark() -> BenchmarkReport:
    """Run all benchmark scenarios and return a report."""
    report = BenchmarkReport()
    # Each scenario owns its isolated workspace (with .coding/ storage inside),
    # so no shared storage root is needed.
    for scenario in SCENARIOS:
        result = await run_scenario(scenario)
        report.results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.name} ({result.category}) - {result.detail or 'ok'}")
    return report


def _write_reports(report: BenchmarkReport) -> tuple[Path, Path, Path]:
    results_dir = Path("evals/coding/results")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    results_dir.mkdir(parents=True, exist_ok=True)
    d = report.to_dict()
    d["timestamp"] = timestamp

    json_path = results_dir / f"{timestamp}-report.json"
    json_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [f"# Sage V6 Benchmark Report ({timestamp})", "", "## Metrics"]
    for key, value in d["metrics"].items():
        md_lines.append(f"- **{key}**: {value}")
    md_lines.append("")
    md_lines.append("## Results")
    md_lines.append("| Scenario | Category | Status | Tool Calls | Duration | Detail |")
    md_lines.append("|----------|----------|--------|------------|----------|--------|")
    for r in d["results"]:
        md_lines.append(
            f"| {r['name']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} "
            f"| {r['tool_calls']} | {r['duration_ms']}ms | {r['detail']} |"
        )
    md_path = results_dir / f"{timestamp}-report.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    html_path = results_dir / f"{timestamp}-report.html"
    generate_html_report(d, html_path)
    return json_path, md_path, html_path


def main() -> None:
    """Run the benchmark and save results."""
    print("Sage V6 Benchmark")
    print("=" * 60)
    report = asyncio.run(run_benchmark())
    print()
    print("Metrics:")
    d = report.to_dict()
    for key, value in d["metrics"].items():
        print(f"  {key}: {value}")
    print()

    json_path, md_path, html_path = _write_reports(report)
    print(f"Report saved to {json_path}, {md_path}, and {html_path}")


if __name__ == "__main__":
    main()
