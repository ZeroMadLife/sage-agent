"""Shared deterministic parser helpers."""

from __future__ import annotations

import hashlib

from .errors import DocumentParseError


def stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode()
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:32]}"


def decode_utf8(payload: bytes, *, document_kind: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParseError(f"{document_kind} source must be UTF-8") from exc
