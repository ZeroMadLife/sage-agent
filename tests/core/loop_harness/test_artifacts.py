from __future__ import annotations

import pytest

from core.loop_harness.artifacts import ArtifactStore
from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.models import ValidationResult, ValidationStep


def _validation() -> ValidationResult:
    return ValidationResult(True, (ValidationStep("frontend-test", 0, 1.25),))


def test_artifact_store_writes_private_patch_and_validation_json(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "reports", max_total_bytes=1024 * 1024)

    receipt = store.save_shadow(
        run_id="loop-20260716-test",
        patch="diff --git a/view.vue b/view.vue\n+class fixed\n",
        validation=_validation(),
    )

    assert receipt.size_bytes > 0
    assert receipt.sha256
    assert (receipt.directory / "shadow.patch").is_file()
    assert (receipt.directory / "validation.json").is_file()
    assert receipt.directory.stat().st_mode & 0o777 == 0o700
    assert (receipt.directory / "shadow.patch").stat().st_mode & 0o777 == 0o600
    store.remove(receipt.directory)
    assert not receipt.directory.exists()


def test_artifact_store_rejects_secret_like_patch(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "reports", max_total_bytes=1024 * 1024)

    with pytest.raises(LoopBlockedError) as exc:
        store.save_shadow(
            run_id="loop-20260716-secret",
            patch='+ token = "ghp_123456789012345678901234567890123456"\n',
            validation=_validation(),
        )

    assert exc.value.code == "BLOCKED_SECRET"
    assert not (tmp_path / "reports/loop-20260716-secret").exists()
