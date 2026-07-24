"""Immutable public package and bounded retrieval coverage."""

import hashlib
import json
from pathlib import Path

import pytest

from public_agent.corpus import PublicPackage

PACKAGE = Path("data/public/sage-public-v1.json")


def test_in_memory_payload_uses_the_same_validation_contract() -> None:
    payload = json.loads(PACKAGE.read_text(encoding="utf-8"))

    assert PublicPackage.from_payload(payload) == PublicPackage.load(PACKAGE)


def test_public_package_verifies_digests_and_retrieves_bounded_sources() -> None:
    package = PublicPackage.load(PACKAGE)

    results = package.retrieve("Harness 如何审批恢复 durable timeline？", limit=2)

    assert package.package_id == "sage-public"
    assert package.revision == "2026-07-24.3"
    assert results[0].document_id == "harness-2"
    assert all(item.document_id in {"harness-2", "sage-challenges"} for item in results)
    assert results[0].url.startswith("https://")
    assert len(results[0].content) <= 420


def test_public_package_retrieves_the_bounded_identity_document() -> None:
    package = PublicPackage.load(PACKAGE)

    results = package.retrieve("你是谁？")

    assert [item.document_id for item in results] == ["sage-identity"]


@pytest.mark.parametrize(
    "question",
    [
        "请介绍一下这个项目",
        "为什么要做 Sage？",
        "它和普通聊天机器人有什么区别？",
        "这个项目有哪些工程亮点？",
        "项目使用了什么技术栈？",
        "整体架构是怎么设计的？",
        "你在这个项目中做了什么？",
        "作者是什么背景，求职方向是什么？",
        "开发过程中最难的地方是什么？",
        "如何保证公开 Agent 不泄露私人数据？",
        "项目怎么做测试和质量保障？",
        "你们怎么部署、备份和回滚？",
        "这个项目现在做到什么程度了？",
        "后续还准备做什么？",
    ],
)
def test_public_package_covers_common_hr_questions(question: str) -> None:
    package = PublicPackage.load(PACKAGE)

    assert package.retrieve(question), question


def test_public_package_describes_the_current_retrieval_and_roadmap_boundaries() -> None:
    package = PublicPackage.load(PACKAGE)
    documents = {item.document_id: item.content for item in package.documents}

    architecture = documents["sage-architecture"]
    assert "SQLite FTS5" in architecture
    assert "hashing" in architecture
    assert "pgvector 是可替换方向" in architecture

    roadmap = documents["sage-roadmap"]
    assert "HR 首页、README 和真实产品画廊已经上线" in roadmap
    assert "新用户首次进入" in roadmap
    assert "完善 HR 首页、README" not in roadmap


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

    with pytest.raises(ValueError, match="unknown or missing fields"):
        PublicPackage.load(modified)


def test_public_package_rejects_unknown_metadata_even_when_it_is_not_secret(
    tmp_path: Path,
) -> None:
    payload = json.loads(PACKAGE.read_text(encoding="utf-8"))
    payload["owner_email"] = "public@example.com"
    modified = tmp_path / "unknown-metadata.json"
    modified.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown or missing fields"):
        PublicPackage.load(modified)


def test_public_retrieval_does_not_fallback_to_unrelated_documents() -> None:
    package = PublicPackage.load(PACKAGE)

    assert package.retrieve("天气和旅游路线") == ()
