"""Deterministic startup probes for the minimal desktop profile."""

from __future__ import annotations

import importlib
import sqlite3
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import CheckpointMetadata, empty_checkpoint
from sage_harness.runtime.checkpoint import open_sqlite_checkpointer, thread_config

ProbeStatus = Literal["ready", "blocked"]

STORAGE_SCHEMA_VERSION = "1"
CHECKPOINT_SCHEMA_VERSION = "1"
CORE_IMPORTS = (
    "aiosqlite",
    "cryptography",
    "cryptography.x509",
    "fastapi",
    "httpx",
    "langgraph",
    "langgraph.checkpoint.sqlite.aio",
    "psycopg2",
    "sage_harness",
    "uvicorn",
)


@dataclass(frozen=True)
class ProbeCheck:
    """One public readiness check without private diagnostic contents."""

    status: ProbeStatus
    version: str
    reason_code: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {"status": self.status, "version": self.version}
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code
        return payload


@dataclass(frozen=True)
class StartupSmoke:
    """Aggregate result used by readiness and packaged-artifact verification."""

    checks: dict[str, ProbeCheck]

    @property
    def overall_status(self) -> ProbeStatus:
        if all(check.status == "ready" for check in self.checks.values()):
            return "ready"
        return "blocked"


def _storage_smoke(data_dir: Path) -> ProbeCheck:
    database_path = data_dir / "sage.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS desktop_metadata "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO desktop_metadata(key, value) VALUES (?, ?)",
            ("schema_version", STORAGE_SCHEMA_VERSION),
        )
        row = connection.execute(
            "SELECT value FROM desktop_metadata WHERE key = ?", ("schema_version",)
        ).fetchone()
    if row != (STORAGE_SCHEMA_VERSION,):
        raise RuntimeError("desktop SQLite read/write verification failed")
    return ProbeCheck(status="ready", version=STORAGE_SCHEMA_VERSION)


async def _checkpoint_smoke(data_dir: Path) -> ProbeCheck:
    checkpoint_path = data_dir / "checkpoints.sqlite3"
    raw_config = thread_config("desktop-packaging-smoke")
    raw_config["configurable"]["checkpoint_ns"] = ""
    config = cast(RunnableConfig, raw_config)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"]["desktop_smoke"] = "checkpoint-reopened"
    checkpoint["channel_versions"]["desktop_smoke"] = "1"
    metadata = CheckpointMetadata(source="input", step=0, parents={})

    async with open_sqlite_checkpointer(checkpoint_path) as saver:
        await saver.aput(
            config,
            checkpoint,
            metadata,
            {"desktop_smoke": "1"},
        )

    async with open_sqlite_checkpointer(checkpoint_path) as reopened:
        checkpoint_tuple = await reopened.aget_tuple(config)
        values = checkpoint_tuple.checkpoint.get("channel_values", {}) if checkpoint_tuple else {}
        if values.get("desktop_smoke") != "checkpoint-reopened":
            raise RuntimeError("desktop checkpoint reopen verification failed")
    return ProbeCheck(status="ready", version=CHECKPOINT_SCHEMA_VERSION)


async def _tls_smoke() -> ProbeCheck:
    context = ssl.create_default_context()
    async with httpx.AsyncClient(verify=context) as client:
        if client.is_closed:
            raise RuntimeError("TLS provider client initialization failed")
    return ProbeCheck(status="ready", version=ssl.OPENSSL_VERSION.split()[1])


def _core_import_smoke() -> ProbeCheck:
    for module_name in CORE_IMPORTS:
        importlib.import_module(module_name)
    return ProbeCheck(status="ready", version=str(len(CORE_IMPORTS)))


async def run_startup_smoke(data_dir: Path) -> StartupSmoke:
    """Run all local-only D0 probes and return stable public reason codes."""
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    checks: dict[str, ProbeCheck] = {}
    probes = (
        ("storage", lambda: _storage_smoke(data_dir)),
        ("checkpoint", lambda: _checkpoint_smoke(data_dir)),
        ("tls", _tls_smoke),
        ("core_imports", _core_import_smoke),
    )
    for name, probe in probes:
        try:
            result = probe()
            checks[name] = await result if hasattr(result, "__await__") else result
        except Exception:
            checks[name] = ProbeCheck(
                status="blocked",
                version="unknown",
                reason_code=f"{name}_probe_failed",
            )
    return StartupSmoke(checks=checks)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CORE_IMPORTS",
    "STORAGE_SCHEMA_VERSION",
    "ProbeCheck",
    "StartupSmoke",
    "run_startup_smoke",
]
