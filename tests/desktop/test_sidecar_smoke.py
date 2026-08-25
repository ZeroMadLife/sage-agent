"""Storage, checkpoint, TLS and import smoke contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from desktop.sidecar.smoke import run_startup_smoke


def test_startup_smoke_reopens_sqlite_checkpoint_and_initializes_tls(tmp_path: Path) -> None:
    first = asyncio.run(run_startup_smoke(tmp_path))
    second = asyncio.run(run_startup_smoke(tmp_path))

    assert first.overall_status == "ready"
    assert second.overall_status == "ready"
    assert first.checks["storage"].version == "1"
    assert first.checks["checkpoint"].version == "1"
    assert first.checks["tls"].status == "ready"
    assert first.checks["core_imports"].status == "ready"
    assert second.checks["checkpoint"].status == "ready"
