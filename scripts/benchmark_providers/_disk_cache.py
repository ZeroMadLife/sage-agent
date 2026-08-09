"""Revision-bound local vector cache for resumable cloud benchmarks."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from array import array
from pathlib import Path


class EmbeddingDiskCache:
    """Persist rebuildable vectors without storing source text or credentials."""

    def __init__(self, *, namespace: str, dimensions: int) -> None:
        self._namespace = namespace
        self._dimensions = dimensions
        self._lock = threading.Lock()
        cache_dir = Path(
            os.environ.get(
                "SAGE_BOOK_EMBEDDING_CACHE_DIR",
                ".coding/evals/embedding-cache",
            )
        ).resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            cache_dir / "book-learning-cloud-v1.sqlite3",
            check_same_thread=False,
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                namespace TEXT NOT NULL,
                role TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                PRIMARY KEY (namespace, role, text_sha256)
            )
            """
        )
        self._connection.commit()

    def get(self, text: str, *, role: str) -> tuple[float, ...] | None:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT dimensions, vector FROM embeddings
                WHERE namespace = ? AND role = ? AND text_sha256 = ?
                """,
                (self._namespace, role, digest),
            ).fetchone()
        if row is None or int(row[0]) != self._dimensions:
            return None
        values = array("d")
        values.frombytes(bytes(row[1]))
        if len(values) != self._dimensions:
            return None
        return tuple(values)

    def put(self, text: str, vector: tuple[float, ...], *, role: str) -> None:
        if len(vector) != self._dimensions:
            raise ValueError("cached embedding dimensions changed")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload = array("d", vector).tobytes()
        with self._lock:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO embeddings
                    (namespace, role, text_sha256, dimensions, vector)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self._namespace, role, digest, self._dimensions, payload),
            )
            self._connection.commit()
