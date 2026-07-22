"""Immutable public package and bounded retrieval coverage."""

import hashlib
import json
from pathlib import Path

import pytest

from public_agent.corpus import PublicPackage

PACKAGE = Path("data/public/sage-public-v1.json")


def test_public_package_verifies_digests_and_retrieves_bounded_sources() -> None:
    package = PublicPackage.load(PACKAGE)

    results = package.retrieve("Harness 如何审批恢复 durable timeline？", limit=2)

    assert package.package_id == "sage-public"
    assert package.revision == "2026-07-22.1"
    assert [item.document_id for item in results] == ["harness-2"]
    assert results[0].url.startswith("https://")
    assert len(results[0].content) <= 420


def test_public_package_rejects_modified_content(tmp_path: Path) -> None:
    payload = json.loads(PACKAGE.read_text(encoding="utf-8"))
    payload["documents"][0]["content"] += " private"
    modified = tmp_path / "modified.json"
    modified.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="digest mismatch"):
        PublicPackage.load(modified)


def test_public_package_rejects_disclosures_even_with_a_valid_digest(tmp_path: Path) -> None:
    payload = json.loads(PACKAGE.read_text(encoding="utf-8"))
    content = payload["documents"][0]["content"] + " path=/Users/owner/private.md"
    payload["documents"][0]["content"] = content
    payload["documents"][0]["content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    modified = tmp_path / "disclosure.json"
    modified.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden disclosure"):
        PublicPackage.load(modified)


def test_public_package_rejects_secret_in_an_unknown_json_field(tmp_path: Path) -> None:
    payload = json.loads(PACKAGE.read_text(encoding="utf-8"))
    payload["private_api_key"] = "must-not-ship"
    modified = tmp_path / "unknown-field.json"
    modified.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden disclosure"):
        PublicPackage.load(modified)


def test_public_retrieval_does_not_fallback_to_unrelated_documents() -> None:
    package = PublicPackage.load(PACKAGE)

    assert package.retrieve("天气和旅游路线") == ()
