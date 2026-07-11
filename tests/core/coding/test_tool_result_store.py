from __future__ import annotations

import stat

import pytest

from core.coding.persistence import tool_result_store as tool_result_module
from core.coding.persistence.tool_result_store import (
    PREVIEW_CHARS,
    ToolResultStore,
)


def test_large_tool_result_is_written_before_preview(tmp_path):
    store = ToolResultStore(tmp_path, "s1", "run_1")
    result = store.archive("call_1", "x" * 20_000)

    assert result.artifact_path.is_file()
    assert result.artifact_path.read_text(encoding="utf-8") == "x" * 20_000
    assert result.preview.endswith("[full result: call_1]")
    assert len(result.preview) < 20_000


def test_archive_atomically_overwrites_and_keeps_private_mode(tmp_path):
    store = ToolResultStore(tmp_path, "s1", "run_1")
    first = store.archive("call_1", "first")
    second = store.archive("call_1", "second")

    assert first.artifact_path == second.artifact_path
    assert second.artifact_path.read_text(encoding="utf-8") == "second"
    assert stat.S_IMODE(second.artifact_path.stat().st_mode) == 0o600
    assert list(store.root.iterdir()) == [second.artifact_path]


def test_preview_keeps_head_and_tail_lines(tmp_path):
    content = "\n".join(f"line-{index:03d}" for index in range(250))
    result = ToolResultStore(tmp_path, "s1", "run_1").archive("call_1", content)

    assert "line-000" in result.preview
    assert "line-119" in result.preview
    assert "line-120" not in result.preview
    assert "line-169" not in result.preview
    assert "line-170" in result.preview
    assert "line-249" in result.preview
    assert result.preview.endswith("[full result: call_1]")
    assert result.truncated is True
    assert result.original_chars == len(content)


def test_preview_is_bounded_by_character_cap(tmp_path):
    content = "x" * (PREVIEW_CHARS + 500)
    result = ToolResultStore(tmp_path, "s1", "run_1").archive("call_1", content)

    assert len(result.preview) <= PREVIEW_CHARS
    assert result.preview.endswith("[full result: call_1]")
    assert result.truncated is True


def test_small_result_is_not_truncated(tmp_path):
    result = ToolResultStore(tmp_path, "s1", "run_1").archive("call_1", "short")

    assert result.preview == "short"
    assert result.truncated is False
    assert result.artifact_ref == "call_1.txt"


@pytest.mark.parametrize(
    ("session_id", "run_id"),
    [
        ("", "run_1"),
        (".", "run_1"),
        ("..", "run_1"),
        ("bad/session", "run_1"),
        (r"bad\session", "run_1"),
        ("s1", ""),
        ("s1", "."),
        ("s1", ".."),
        ("s1", "bad/run"),
        ("s1", r"bad\run"),
    ],
)
def test_tool_result_store_rejects_invalid_scope_ids(tmp_path, session_id, run_id):
    with pytest.raises(ValueError):
        ToolResultStore(tmp_path, session_id, run_id)


@pytest.mark.parametrize("call_id", ["", ".", "..", "bad/call", r"bad\call"])
def test_archive_rejects_invalid_call_ids(tmp_path, call_id):
    store = ToolResultStore(tmp_path, "s1", "run_1")

    with pytest.raises(ValueError):
        store.archive(call_id, "content")


def test_tool_store_rejects_symlinked_result_directory_before_outside_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    result_root = tmp_path / "evidence" / "s1" / "runs" / "run_1" / "tool-results"
    result_root.parent.mkdir(parents=True)
    result_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        ToolResultStore(tmp_path, "s1", "run_1")

    assert list(outside.iterdir()) == []


def test_archive_rejects_symlinked_artifact_before_outside_write(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("original", encoding="utf-8")
    store = ToolResultStore(tmp_path, "s1", "run_1")
    store.root.mkdir(parents=True)
    (store.root / "call_1.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        store.archive("call_1", "replacement")

    assert outside.read_text(encoding="utf-8") == "original"


def test_artifact_is_durable_before_preview_failure(tmp_path, monkeypatch):
    store = ToolResultStore(tmp_path, "s1", "run_1")

    def fail_preview(content, call_id):
        raise RuntimeError("preview failed")

    monkeypatch.setattr(tool_result_module, "_bounded_preview", fail_preview)

    with pytest.raises(RuntimeError, match="preview failed"):
        store.archive("call_1", "complete result")

    assert (store.root / "call_1.txt").read_text(encoding="utf-8") == "complete result"
    assert list(store.root.iterdir()) == [store.root / "call_1.txt"]
