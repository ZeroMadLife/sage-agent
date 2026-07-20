"""MinerU Agent API adapter with signed upload and bounded Markdown download."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import httpx

from ..external import (
    ExternalAdapterError,
    ExternalParseCompleted,
    ExternalParseDispatch,
    ExternalParsePending,
    ExternalParseProgress,
    ExternalParseStage,
    ExternalParseTicket,
    ProgressCallback,
)
from ..types import ParsedDocument, ParseRequest
from .document import external_markdown_document
from .http import download_bounded_text, json_object, request, require_https_url

_MINERU_HOSTS = ("mineru.net", ".mineru.net")
_MINERU_ASSET_HOSTS = (".openxlab.org.cn", ".aliyuncs.com", ".mineru.net")


@dataclass(frozen=True, slots=True)
class MinerUConfig:
    base_url: str = "https://mineru.net/api/v1/agent"
    poll_seconds: float = 1.0


class MinerUAdapter:
    adapter_id = "mineru.agent"
    adapter_version = "1.1.0"
    media_types = frozenset({"application/pdf"})

    def __init__(
        self,
        config: MinerUConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._base_url = require_https_url(
            config.base_url.rstrip("/"), allowed_suffixes=_MINERU_HOSTS
        )
        if config.poll_seconds <= 0 or config.poll_seconds > 30:
            raise ValueError("MinerU poll interval must be between 0 and 30 seconds")

    async def parse(
        self,
        request_value: ParseRequest,
        *,
        progress: ProgressCallback,
    ) -> ParsedDocument:
        outcome: ExternalParseDispatch = await self.submit(request_value, progress=progress)
        while isinstance(outcome, ExternalParsePending):
            await asyncio.sleep(outcome.retry_after_seconds)
            outcome = await self.resume(
                request_value,
                outcome.ticket,
                progress=progress,
            )
        return outcome.document

    async def submit(
        self,
        request_value: ParseRequest,
        *,
        progress: ProgressCallback,
    ) -> ExternalParsePending:
        """Create and upload one task, then return without polling it."""

        if len(request_value.payload) > 10 * 1024 * 1024:
            raise ExternalAdapterError(self.adapter_id, "input_too_large", retryable=False)
        file_name = PurePosixPath(request_value.relative_path).name
        async with self._client_scope() as client:
            created = await request(
                client,
                self.adapter_id,
                "POST",
                f"{self._base_url}/parse/file",
                json={
                    "file_name": file_name,
                    "language": "ch",
                    "enable_table": True,
                    "is_ocr": True,
                    "enable_formula": True,
                },
            )
            data = self._success_data(created)
            task_id = _required_identifier(data, "task_id", self.adapter_id)
            upload_url = self._asset_url(_required_string(data, "file_url", self.adapter_id))
            await progress(
                ExternalParseProgress(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    stage="uploading",
                )
            )
            await request(
                client,
                self.adapter_id,
                "PUT",
                upload_url,
                content=request_value.payload,
            )
            await progress(
                ExternalParseProgress(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    stage="queued",
                )
            )
        return ExternalParsePending(
            ticket=ExternalParseTicket(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                task_id=task_id,
            ),
            stage="queued",
            retry_after_seconds=self.config.poll_seconds,
        )

    async def resume(
        self,
        request_value: ParseRequest,
        ticket: ExternalParseTicket,
        *,
        progress: ProgressCallback,
    ) -> ExternalParseDispatch:
        """Poll exactly once so the Knowledge worker never waits on MinerU."""

        if (
            ticket.adapter_id != self.adapter_id
            or ticket.adapter_version != self.adapter_version
            or not _IDENTIFIER.fullmatch(ticket.task_id)
        ):
            raise ExternalAdapterError(self.adapter_id, "invalid_ticket", retryable=False)
        async with self._client_scope() as client:
            response = await request(
                client,
                self.adapter_id,
                "GET",
                f"{self._base_url}/parse/{ticket.task_id}",
            )
            data = self._success_data(response)
            state = _required_string(data, "state", self.adapter_id)
            if state == "done":
                markdown_url = self._asset_url(
                    _required_string(data, "markdown_url", self.adapter_id)
                )
                markdown = await download_bounded_text(
                    client,
                    self.adapter_id,
                    markdown_url,
                    max_bytes=4 * 1024 * 1024,
                )
                try:
                    return ExternalParseCompleted(
                        external_markdown_document(
                            request_value,
                            markdown,
                            parser_id=self.adapter_id,
                            parser_version=self.adapter_version,
                            title=PurePosixPath(request_value.relative_path).stem,
                            language="zh",
                            confidence=0.9,
                        )
                    )
                except ValueError as exc:
                    raise ExternalAdapterError(
                        self.adapter_id, "invalid_result", retryable=False
                    ) from exc
            if state == "failed":
                raw_code = data.get("err_code")
                code = f"upstream_{raw_code}" if isinstance(raw_code, int | str) else "failed"
                retryable = raw_code in {-10001, "-10001"}
                raise ExternalAdapterError(self.adapter_id, code, retryable=retryable)
            if state not in {
                "waiting-file",
                "uploading",
                "pending",
                "running",
                "converting",
            }:
                raise ExternalAdapterError(self.adapter_id, "invalid_state", retryable=False)
            stage: ExternalParseStage = (
                "running" if state in {"running", "converting"} else "queued"
            )
            await progress(
                ExternalParseProgress(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    stage=stage,
                )
            )
            return ExternalParsePending(
                ticket=ticket,
                stage=stage,
                retry_after_seconds=self.config.poll_seconds,
            )

    def _success_data(self, response: httpx.Response) -> dict[str, Any]:
        payload = json_object(response, self.adapter_id)
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict):
            raw_code = payload.get("code")
            retryable = raw_code in {-10001, -60001, "-10001", "-60001"}
            code = f"upstream_{raw_code}" if isinstance(raw_code, int | str) else "invalid_json"
            raise ExternalAdapterError(self.adapter_id, code, retryable=retryable)
        return data

    def _asset_url(self, value: str) -> str:
        try:
            return require_https_url(
                value,
                allowed_suffixes=_MINERU_ASSET_HOSTS,
                allow_query=True,
            )
        except ValueError as exc:
            raise ExternalAdapterError(self.adapter_id, "unsafe_url", retryable=False) from exc

    @asynccontextmanager
    async def _client_scope(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=60.0,
        ) as client:
            yield client


def _required_string(data: dict[str, Any], key: str, adapter_id: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ExternalAdapterError(adapter_id, "invalid_json", retryable=False)
    return value.strip()


_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _required_identifier(data: dict[str, Any], key: str, adapter_id: str) -> str:
    value = _required_string(data, key, adapter_id)
    if not _IDENTIFIER.fullmatch(value):
        raise ExternalAdapterError(adapter_id, "invalid_json", retryable=False)
    return value
