"""Immutable public package loading and bounded lexical retrieval."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ASCII_TERM = re.compile(r"[a-z0-9][a-z0-9._-]+", re.IGNORECASE)
_HAN_RUN = re.compile(r"[\u3400-\u9fff]+")
_FORBIDDEN_DISCLOSURES = (
    re.compile(r"/(?:Users|home|etc|opt/sage)/", re.IGNORECASE),
    re.compile(
        r"(?:api[_ -]?key|token|secret|password)[\"']?\s*[:=]\s*[\"']?\S+",
        re.IGNORECASE,
    ),
    re.compile(r"(?:sk-|ghp_|github_pat_)[a-z0-9_-]{12,}", re.IGNORECASE),
)
_PACKAGE_KEYS = frozenset({"package_id", "revision", "documents"})
_DOCUMENT_KEYS = frozenset({"id", "title", "url", "revision", "content", "content_sha256"})


@dataclass(frozen=True, slots=True)
class PublicDocument:
    document_id: str
    title: str
    url: str
    revision: str
    content: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class PublicPackage:
    package_id: str
    revision: str
    digest: str
    documents: tuple[PublicDocument, ...]

    @classmethod
    def load(cls, path: str | Path) -> PublicPackage:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: Any) -> PublicPackage:
        """Validate one in-memory package with the same rules as file loading."""
        if not isinstance(payload, dict):
            raise ValueError("public package must be a JSON object")
        if set(payload) != _PACKAGE_KEYS:
            raise ValueError("public package contains unknown or missing fields")
        canonical_payload = _canonical_json(payload)
        if any(pattern.search(canonical_payload) for pattern in _FORBIDDEN_DISCLOSURES):
            raise ValueError("public package contains a forbidden disclosure")
        documents = tuple(_load_document(item) for item in _document_items(payload))
        if not documents:
            raise ValueError("public package must contain at least one document")
        document_ids = [item.document_id for item in documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("public package document ids must be unique")
        package_id = _required_text(payload, "package_id")
        revision = _required_text(payload, "revision")
        digest = hashlib.sha256(canonical_payload.encode()).hexdigest()
        return cls(package_id=package_id, revision=revision, digest=digest, documents=documents)

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 3,
        excerpt_chars: int = 420,
    ) -> tuple[PublicDocument, ...]:
        if limit < 1 or limit > 5:
            raise ValueError("public retrieval limit must be between 1 and 5")
        query_terms = _terms(query)
        if not query_terms:
            return ()
        ranked: list[tuple[int, str, PublicDocument]] = []
        for document in self.documents:
            title_terms = _terms(document.title)
            content_terms = _terms(document.content)
            title_score = len(query_terms & title_terms) * 4
            content_score = len(query_terms & content_terms)
            score = title_score + content_score
            if score > 0:
                ranked.append((score, document.document_id, document))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if not ranked:
            return ()
        minimum_score = max(2, (ranked[0][0] + 2) // 3)
        selected = (item for item in ranked if item[0] >= minimum_score)
        return tuple(_bounded_document(item[2], excerpt_chars) for item in list(selected)[:limit])


def _document_items(payload: dict[str, Any]) -> list[Any]:
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise ValueError("public package documents must be a list")
    return documents


def _load_document(value: Any) -> PublicDocument:
    if not isinstance(value, dict):
        raise ValueError("public package document must be an object")
    if set(value) != _DOCUMENT_KEYS:
        raise ValueError("public package document contains unknown or missing fields")
    content = _required_text(value, "content")
    actual_digest = hashlib.sha256(content.encode()).hexdigest()
    expected_digest = _required_text(value, "content_sha256").lower()
    if actual_digest != expected_digest:
        raise ValueError("public package document digest mismatch")
    url = _required_text(value, "url")
    if not url.startswith("https://"):
        raise ValueError("public package document URL must use HTTPS")
    return PublicDocument(
        document_id=_required_text(value, "id"),
        title=_required_text(value, "title"),
        url=url,
        revision=_required_text(value, "revision"),
        content=content,
        content_sha256=expected_digest,
    )


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"public package {key} must be a non-empty string")
    return value.strip()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _terms(value: str) -> set[str]:
    normalized = value.casefold()
    terms = {match.group(0) for match in _ASCII_TERM.finditer(normalized)}
    for run in _HAN_RUN.findall(normalized):
        terms.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
        if len(run) == 1:
            terms.add(run)
    return terms


def _bounded_document(document: PublicDocument, excerpt_chars: int) -> PublicDocument:
    content = document.content
    if len(content) > excerpt_chars:
        content = content[: max(1, excerpt_chars - 1)].rstrip() + "..."
    return PublicDocument(
        document_id=document.document_id,
        title=document.title,
        url=document.url,
        revision=document.revision,
        content=content,
        content_sha256=document.content_sha256,
    )
