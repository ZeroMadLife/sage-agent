"""Strict stdin contract for the root-owned public package controller."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from public_agent.registry import PublishedPackageError, PublishedPackageRegistry
from scripts.public_packagectl import execute, parse_request

ROOT = Path(__file__).resolve().parents[2]


def test_controller_import_does_not_require_the_web_runtime() -> None:
    script = """
import datetime as runtime_datetime
import importlib.abc
import sys
import types

class BlockWebRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] in {'fastapi', 'openai'}:
            raise ModuleNotFoundError(f'blocked optional runtime: {fullname}')
        return None

sys.meta_path.insert(0, BlockWebRuntime())

python_310_datetime = types.ModuleType('datetime')
for name in dir(runtime_datetime):
    if name != 'UTC':
        setattr(python_310_datetime, name, getattr(runtime_datetime, name))
sys.modules['datetime'] = python_310_datetime

import scripts.public_packagectl
print('packagectl-imported')
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "packagectl-imported"


def test_request_contract_accepts_exact_lifecycle_actions() -> None:
    assert parse_request(io.StringIO('{"action":"status"}')) == {"action": "status"}
    publish = {
        "action": "publish",
        "package_id": "sage-public",
        "revision": "v2",
        "expected_active_revision": "v1",
    }
    assert parse_request(io.StringIO(json.dumps(publish))) == publish

    with pytest.raises(PublishedPackageError, match="字段无效"):
        parse_request(io.StringIO('{"action":"status","shell":"id"}'))
    with pytest.raises(PublishedPackageError, match="字段无效"):
        parse_request(io.StringIO('{"action":"delete","revision":"v1"}'))
    with pytest.raises(PublishedPackageError, match="必须是 string"):
        parse_request(
            io.StringIO(
                '{"action":"publish","package_id":1,"revision":"v2",'
                '"expected_active_revision":"v1"}'
            )
        )
    with pytest.raises(PublishedPackageError, match="reason 必须是 string"):
        parse_request(
            io.StringIO(
                '{"action":"revoke","package_id":"sage-public","revision":"v2",'
                '"expected_active_revision":"v2","reason":1}'
            )
        )


def test_controller_stages_payload_without_accepting_an_arbitrary_source_path(
    tmp_path: Path,
) -> None:
    payload = json.loads(Path("data/public/sage-public-v1.json").read_text(encoding="utf-8"))
    registry = PublishedPackageRegistry(tmp_path)
    request = parse_request(
        io.StringIO(json.dumps({"action": "stage", "package": payload}, ensure_ascii=False))
    )

    result = execute(registry, request, actor="sage-deploy")

    assert result["status"] == "staged"
    assert result["active_revision"] is None
    assert (tmp_path / "packages/sage-public/2026-07-24.3.json").is_file()
