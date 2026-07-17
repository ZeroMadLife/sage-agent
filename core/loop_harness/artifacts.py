"""Private, quota-bound evidence storage outside the Git repository."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.models import ArtifactReceipt, ValidationResult

_RUN_ID = re.compile(r"[a-z0-9-]{8,80}")
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(
        rb"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*[\"'][^\"'\r\n]{12,}[\"']"
    ),
)


class ArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        max_total_bytes: int = 1024 * 1024 * 1024,
        max_patch_bytes: int = 256 * 1024,
    ) -> None:
        self.root = root
        self.max_total_bytes = max_total_bytes
        self.max_patch_bytes = max_patch_bytes

    def save_shadow(
        self,
        *,
        run_id: str,
        patch: str,
        validation: ValidationResult,
    ) -> ArtifactReceipt:
        if _RUN_ID.fullmatch(run_id) is None:
            raise LoopBlockedError("BLOCKED_ARTIFACT", "artifact run id is invalid")
        _ensure_private_root(self.root)
        target = self.root / run_id
        if target.exists() or target.is_symlink():
            raise LoopBlockedError("BLOCKED_ARTIFACT", "artifact directory already exists")

        patch_bytes = patch.encode("utf-8")
        if len(patch_bytes) > self.max_patch_bytes:
            raise LoopBlockedError("BLOCKED_ARTIFACT_SIZE", "shadow patch exceeds 256 KiB")
        if any(pattern.search(patch_bytes) for pattern in _SECRET_PATTERNS):
            raise LoopBlockedError("BLOCKED_SECRET", "shadow patch contains secret-like data")
        validation_bytes = (
            json.dumps(
                {
                    "passed": validation.passed,
                    "steps": [
                        {
                            "name": step.name,
                            "exit_code": step.exit_code,
                            "duration_seconds": step.duration_seconds,
                        }
                        for step in validation.steps
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        size_bytes = len(patch_bytes) + len(validation_bytes)
        if _tree_size(self.root) + size_bytes > self.max_total_bytes:
            raise LoopBlockedError("BLOCKED_ARTIFACT_QUOTA", "Loop evidence exceeds 1 GiB")

        temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=self.root))
        try:
            temporary.chmod(0o700)
            patch_path = temporary / "shadow.patch"
            validation_path = temporary / "validation.json"
            patch_path.write_bytes(patch_bytes)
            validation_path.write_bytes(validation_bytes)
            patch_path.chmod(0o600)
            validation_path.chmod(0o600)
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return ArtifactReceipt(
            directory=target,
            sha256=hashlib.sha256(patch_bytes).hexdigest(),
            size_bytes=size_bytes,
        )

    def remove(self, directory: Path) -> None:
        if not directory.exists():
            return
        if directory.is_symlink() or directory.parent.resolve() != self.root.resolve():
            raise LoopBlockedError("BLOCKED_ARTIFACT_CLEANUP", "artifact path is outside store")
        if _RUN_ID.fullmatch(directory.name) is None:
            raise LoopBlockedError("BLOCKED_ARTIFACT_CLEANUP", "artifact directory name is invalid")
        for path in directory.rglob("*"):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise LoopBlockedError(
                    "BLOCKED_ARTIFACT_CLEANUP", "artifact directory contains unsafe entries"
                )
        shutil.rmtree(directory)


def _ensure_private_root(root: Path) -> None:
    if root.exists() and root.is_symlink():
        raise LoopBlockedError("BLOCKED_ARTIFACT", "artifact root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)


def _tree_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return total
