"""Coding model manifest validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.coding.context import CodingModelManifest


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "models.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_manifest_builds_catalog_and_capabilities_from_one_source(tmp_path: Path) -> None:
    manifest = CodingModelManifest.from_file(
        _write(
            tmp_path,
            """
version = 1
default_model = "provider:model-a"

[[models]]
id = "provider:model-a"
label = "Model A"
provider = "provider"
context_window_tokens = 100000
output_reserve_tokens = 10000
reasoning_modes = ["low", "high"]

[[models]]
id = "provider:model-b"
label = "Model B"
provider = "provider"
""",
        )
    )

    assert manifest.default_model == "provider:model-a"
    assert manifest.catalog == [
        {
            "id": "provider:model-a",
            "label": "Model A",
            "provider": "provider",
            "reasoning_modes": ["low", "high"],
        },
        {
            "id": "provider:model-b",
            "label": "Model B",
            "provider": "provider",
            "reasoning_modes": [],
        },
    ]
    policy = manifest.registry.resolve("provider:model-a")
    assert policy is not None
    assert policy.context_window_tokens == 100_000
    assert policy.output_reserve_tokens == 10_000
    assert manifest.registry.resolve("provider:model-b") is None


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        (
            'version = 2\ndefault_model = "m"\nmodels = []\n',
            "version must be 1",
        ),
        (
            'version = 1\ndefault_model = "missing"\n'
            '[[models]]\nid = "m"\nlabel = "M"\nprovider = "p"\n',
            "default_model must reference",
        ),
        (
            'version = 1\ndefault_model = "m"\n'
            '[[models]]\nid = "m"\nlabel = "M"\nprovider = "p"\n'
            '[[models]]\nid = "m"\nlabel = "M2"\nprovider = "p"\n',
            "duplicate model id",
        ),
        (
            'version = 1\ndefault_model = "m"\n'
            '[[models]]\nid = "m"\nlabel = "M"\nprovider = "p"\n'
            'context_window_tokens = 100000\n',
            "configure context window and output reserve together",
        ),
        (
            'version = 1\ndefault_model = "m"\n'
            '[[models]]\nid = "m"\nlabel = "M"\nprovider = "p"\n'
            'context_window_tokens = 100\noutput_reserve_tokens = 100\n',
            "output reserve must be less",
        ),
        (
            'version = 1\ndefault_model = "m"\n'
            '[[models]]\nid = "m"\nlabel = "M"\nprovider = "p"\n'
            'reasoning_modes = ["raw-chain"]\n',
            "unsupported reasoning mode",
        ),
        (
            'version = 1\ndefault_model = "m"\nunknown = true\n'
            '[[models]]\nid = "m"\nlabel = "M"\nprovider = "p"\n',
            "unknown root fields",
        ),
    ],
)
def test_manifest_rejects_invalid_or_ambiguous_configuration(
    tmp_path: Path, content: str, detail: str
) -> None:
    with pytest.raises(ValueError, match=detail):
        CodingModelManifest.from_file(_write(tmp_path, content))


def test_manifest_reports_missing_or_invalid_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unable to read"):
        CodingModelManifest.from_file(tmp_path / "missing.toml")
    with pytest.raises(ValueError, match="invalid coding model TOML"):
        CodingModelManifest.from_file(_write(tmp_path, "not = [valid"))
