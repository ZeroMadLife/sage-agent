#!/usr/bin/env python3
"""Run a destructive-but-ephemeral Container Sandbox Level 1 audit."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.coding.context import WorkspaceContext
from core.harness.container_sandbox import ContainerWorkspaceSandbox


@dataclass(frozen=True, slots=True)
class AuditCase:
    case_id: str
    passed: bool
    evidence: str


async def run_audit(image: str) -> dict[str, Any]:
    cases: list[AuditCase] = []
    os.environ["SAGE_SANDBOX_AUDIT_SECRET"] = "must-not-cross-boundary"
    with tempfile.TemporaryDirectory(prefix="sage-sandbox-audit-") as temporary:
        workspace = Path(temporary)
        sandbox = ContainerWorkspaceSandbox(
            WorkspaceContext(workspace),
            thread_id="level1-live-audit",
            image=image,
        )
        try:
            health = await sandbox.health()
            if health["status"] in {"missing", "unavailable"}:
                await sandbox.invoke("list_files", {"path": "."})
                health = await sandbox.health()
            cases.append(
                AuditCase(
                    "inspect_profile",
                    health.get("healthy") is True,
                    _bounded(str(health.get("security_violations", []))),
                )
            )

            wrote = await sandbox.invoke(
                "write_file",
                {"path": "audit/allowed.txt", "content": "sandbox-write-ok\n"},
            )
            cases.append(
                AuditCase(
                    "workspace_write",
                    not wrote.is_error and (workspace / "audit" / "allowed.txt").is_file(),
                    _bounded(wrote.content),
                )
            )

            cases.append(
                await _shell_case(
                    sandbox,
                    "read_only_rootfs",
                    "touch /etc/sage-audit-denied",
                    expect_error=True,
                    expected="Read-only file system",
                )
            )
            cases.append(
                await _shell_case(
                    sandbox,
                    "network_disabled",
                    "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 1)\"",
                    expect_error=True,
                )
            )
            cases.append(
                await _shell_case(
                    sandbox,
                    "capabilities_dropped",
                    "awk '/CapEff/ {print $2}' /proc/self/status",
                    expected="0000000000000000",
                )
            )
            cases.append(
                await _shell_case(
                    sandbox,
                    "no_new_privileges",
                    "awk '/NoNewPrivs/ {print $2}' /proc/self/status",
                    expected="1",
                )
            )
            cases.append(
                await _shell_case(
                    sandbox,
                    "isolated_home",
                    "printf %s \"$HOME\"",
                    expected="/tmp/sage-home",
                )
            )
            cases.append(
                await _shell_case(
                    sandbox,
                    "host_environment_not_forwarded",
                    "test -z \"${SAGE_SANDBOX_AUDIT_SECRET+x}\"",
                )
            )
            cases.append(
                await _shell_case(
                    sandbox,
                    "file_size_limited",
                    "python -c \"open('/workspace/audit/oversized.bin','wb').truncate(70000000)\"",
                    expect_error=True,
                    expected="File too large",
                )
            )
        finally:
            await sandbox.aclose()
        closed_health = await sandbox.health()
        cases.append(
            AuditCase(
                "terminal_cleanup",
                closed_health.get("status") == "missing",
                _bounded(str(closed_health.get("status", "unknown"))),
            )
        )

    passed = sum(case.passed for case in cases)
    return {
        "audit_id": "sage-container-sandbox-level1-v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_commit": _git_value("rev-parse", "HEAD"),
        "source_dirty": bool(_git_value("status", "--porcelain")),
        "image": image,
        "case_count": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "pass_rate": passed / len(cases),
        "cases": [asdict(case) for case in cases],
    }


async def _shell_case(
    sandbox: ContainerWorkspaceSandbox,
    case_id: str,
    command: str,
    *,
    expect_error: bool = False,
    expected: str = "",
) -> AuditCase:
    result = await sandbox.invoke("run_shell", {"command": command, "timeout": 5})
    status_matches = result.is_error is expect_error
    content_matches = not expected or expected in result.content
    return AuditCase(case_id, status_matches and content_matches, _bounded(result.content))


def _git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _bounded(value: str) -> str:
    return value.replace(str(Path.home()), "<home>")[:500]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="python:3.11-slim")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_audit(args.image))
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
