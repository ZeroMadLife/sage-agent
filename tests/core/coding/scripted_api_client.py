"""Deterministic model client for coding-agent loop tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class ScriptedApiClient:
    """Return scripted model responses and record prompts.

    Exposes both ``complete`` (full response) and ``astream`` (chunked
    response) so the engine can exercise its streaming path. Responses are
    popped from a shared queue, so whichever interface the engine calls first
    consumes the next scripted reply.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            return "<final>done</final>"
        return self._responses.pop(0)

    async def astream(self, messages: list[dict[str, str]]) -> AsyncIterator[Any]:
        """Yield the scripted response in chunks to simulate streaming."""
        self.calls.append([dict(msg) for msg in messages])
        full = "<final>done</final>" if not self._responses else self._responses.pop(0)
        chunk_size = 10
        for i in range(0, len(full), chunk_size):
            chunk_text = full[i : i + chunk_size]
            yield type("Chunk", (), {"content": chunk_text})()
