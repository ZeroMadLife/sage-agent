"""Deterministic candidate and diff policy for Phase 2."""

from __future__ import annotations

from pathlib import PurePosixPath

from core.loop_harness.models import DiffSnapshot, PolicyDecision, WorkerResult

_MIN_CONFIDENCE = 0.8
_PROTECTED_PREFIXES = (
    ".cc-connect/",
    ".codex/",
    ".github/",
    "core/",
    "db/",
    "docs/loop-harness/",
    "scripts/",
    "frontend/src/api/",
    "frontend/src/router/",
    "frontend/src/stores/",
    "frontend/src/types/",
)
_PROTECTED_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/pnpm-lock.yaml",
    "frontend/yarn.lock",
}
_GLOBAL_DIRTY_PREFIXES = (
    ".github/",
    "frontend/src/api/",
    "frontend/src/router/",
    "frontend/src/stores/",
    "frontend/src/types/",
)
_GLOBAL_DIRTY_FILES = {
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/pnpm-lock.yaml",
    "frontend/yarn.lock",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
}


def classify_candidate(
    candidate: WorkerResult,
    *,
    dirty_paths: tuple[str, ...],
    scan_scope: tuple[str, ...] = (),
) -> PolicyDecision:
    reasons: list[str] = []
    paths = _normalized_paths(candidate.changed_files)
    if candidate.verdict not in {"FIX", "FRONTEND_CANDIDATE"}:
        reasons.append("不是可修复前端候选")
    if candidate.confidence < _MIN_CONFIDENCE:
        reasons.append("候选置信度不足")
    if candidate.suggested_tier == "C":
        reasons.append("模型已识别为只报告范围")
    if not paths:
        reasons.append("候选没有声明修改路径")
    if any(not _is_frontend_path(path) or _is_protected(path) for path in paths):
        reasons.append("候选包含未授权路径")
    if scan_scope and any(not _in_scan_scope(path, scan_scope) for path in paths):
        reasons.append("候选不属于本轮扫描范围")
    if _overlaps_dirty(paths, dirty_paths):
        reasons.append("人工修改路径重叠")
    if reasons:
        return PolicyDecision(False, "C", tuple(reasons))
    return PolicyDecision(True, candidate.suggested_tier, ())


def evaluate_diff(
    candidate: WorkerResult,
    snapshot: DiffSnapshot,
    *,
    dirty_paths: tuple[str, ...],
) -> PolicyDecision:
    candidate_decision = classify_candidate(candidate, dirty_paths=dirty_paths)
    if not candidate_decision.allowed:
        return candidate_decision

    declared = set(_normalized_paths(candidate.changed_files))
    actual = _normalized_paths(snapshot.changed_files)
    reasons: list[str] = []
    if not actual:
        reasons.append("Fixer 没有生成 diff")
    if any(path not in declared for path in actual):
        reasons.append("diff 包含候选范围之外的路径")
    if any(not _is_frontend_path(path) or _is_protected(path) for path in actual):
        reasons.append("diff 包含未授权路径")
    if _overlaps_dirty(actual, dirty_paths):
        reasons.append("diff 与人工修改路径重叠")
    if snapshot.binary_files:
        reasons.append("diff 包含二进制文件")
    if snapshot.symlink_files:
        reasons.append("diff 包含符号链接")
    if snapshot.deleted_files:
        reasons.append("diff 删除了文件")
    if reasons:
        return PolicyDecision(False, "C", tuple(reasons))

    production = tuple(path for path in actual if _is_production_path(path))
    tests = tuple(path for path in actual if _is_test_path(path))
    behavior_is_covered = not snapshot.behavior_changed or bool(tests)
    if (
        production
        and behavior_is_covered
        and len(production) <= 2
        and len(tests) <= 1
        and snapshot.changed_lines <= 80
    ):
        return PolicyDecision(True, "A", ())

    tier_b_reasons: list[str] = []
    if snapshot.behavior_changed:
        tier_b_reasons.append("包含前端行为变化")
    if len(actual) <= 3 and snapshot.changed_lines <= 150:
        return PolicyDecision(True, "B", tuple(tier_b_reasons))
    return PolicyDecision(False, "C", ("diff 超过 Tier B 文件数或行数预算",))


def _normalized_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in paths:
        path = PurePosixPath(raw)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in raw
            or any(character in raw for character in ("\0", "\n", "\r"))
        ):
            normalized.append(f"<invalid>/{raw}")
            continue
        normalized.append(path.as_posix())
    return tuple(dict.fromkeys(normalized))


def _is_frontend_path(path: str) -> bool:
    return _is_production_path(path) or _is_test_path(path)


def _is_production_path(path: str) -> bool:
    return path.endswith(".vue") and path.startswith(
        ("frontend/src/components/", "frontend/src/views/")
    )


def _is_test_path(path: str) -> bool:
    return path.startswith("frontend/src/") and path.endswith(".test.ts")


def _is_protected(path: str) -> bool:
    return path in _PROTECTED_FILES or path.startswith(_PROTECTED_PREFIXES)


def _in_scan_scope(path: str, scan_scope: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(f"{root.rstrip('/')}/") for root in scan_scope)


def _overlaps_dirty(paths: tuple[str, ...], dirty_paths: tuple[str, ...]) -> bool:
    dirty = _normalized_paths(dirty_paths)
    if paths and any(_is_global_dirty(path) for path in dirty):
        return True
    for path in paths:
        for human_path in dirty:
            if path == human_path or _companion_key(path) == _companion_key(human_path):
                return True
    return False


def _is_global_dirty(path: str) -> bool:
    return path in _GLOBAL_DIRTY_FILES or path.startswith(_GLOBAL_DIRTY_PREFIXES)


def _companion_key(path: str) -> str:
    name = PurePosixPath(path).name
    for suffix in (".test.ts", ".spec.ts", ".vue", ".ts"):
        if name.endswith(suffix):
            return name[: -len(suffix)].casefold()
    return name.casefold()
