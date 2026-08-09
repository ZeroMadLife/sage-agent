#!/usr/bin/env python3
"""Fetch the versioned public-domain book-learning corpus outside Git."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MANIFEST = _ROOT / "evals/corpora/book_learning_public_domain_v1.json"
_DEFAULT_OUTPUT = _ROOT / ".coding/corpora/book-learning-public-domain-v1"
_ALLOWED_DOWNLOAD_HOSTS = {"www.gutenberg.org"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    receipts: list[dict[str, object]] = []
    for document in manifest["documents"]:
        destination = args.output_dir / str(document["filename"])
        status = _fetch_document(document, destination, force=args.force)
        receipts.append(
            {
                "document_id": document["document_id"],
                "filename": document["filename"],
                "sha256": document["expected_sha256"],
                "size_bytes": destination.stat().st_size,
                "status": status,
            }
        )
    print(json.dumps({"dataset_id": manifest["dataset_id"], "documents": receipts}, indent=2))
    return 0


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("corpus manifest must contain documents")
    for document in documents:
        if not isinstance(document, dict) or document.get("copyright") is not False:
            raise ValueError("corpus document must be explicitly public domain")
        url = str(document.get("download_url", ""))
        host = urllib.parse.urlparse(url).hostname
        if host not in _ALLOWED_DOWNLOAD_HOSTS:
            raise ValueError("corpus download host is not allowed")
        expected = str(document.get("expected_sha256", ""))
        if len(expected) != 64:
            raise ValueError("corpus document sha256 is invalid")
    return payload


def _fetch_document(document: dict[str, Any], destination: Path, *, force: bool) -> str:
    expected = str(document["expected_sha256"])
    if destination.is_file() and not force and _sha256(destination) == expected:
        return "verified_cached"
    request = urllib.request.Request(
        str(document["download_url"]),
        headers={"User-Agent": "SageBookLearningEval/1.0"},
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with (
            os.fdopen(descriptor, "wb") as output,
            urllib.request.urlopen(request, timeout=60) as response,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = _sha256(temporary)
        if actual != expected:
            raise ValueError(f"corpus checksum mismatch for {document['document_id']}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return "downloaded"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
