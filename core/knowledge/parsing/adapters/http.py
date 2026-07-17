"""Small HTTP safety helpers shared by external parser adapters."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..external import ExternalAdapterError

_MAX_JSON_BYTES = 1024 * 1024


def require_https_url(
    value: str,
    *,
    allowed_suffixes: Collection[str],
    allow_query: bool = False,
) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    allowed = any(
        hostname == suffix.lstrip(".")
        or (suffix.startswith(".") and hostname.endswith(suffix))
        for suffix in allowed_suffixes
    )
    if (
        len(normalized) > 4096
        or parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or (parsed.query and not allow_query)
        or parsed.fragment
        or not allowed
    ):
        raise ValueError("external parser returned an unsafe URL")
    return normalized


async def request(
    client: httpx.AsyncClient,
    adapter_id: str,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    try:
        response = await client.request(method, url, **kwargs)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ExternalAdapterError(adapter_id, "network_error", retryable=True) from exc
    _raise_for_status(response, adapter_id)
    return response


def _raise_for_status(response: httpx.Response, adapter_id: str) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        raise ExternalAdapterError(
            adapter_id,
            f"http_{response.status_code}",
            retryable=True,
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise ExternalAdapterError(
            adapter_id,
            f"http_{response.status_code}",
            retryable=False,
        )


def json_object(response: httpx.Response, adapter_id: str) -> dict[str, Any]:
    if len(response.content) > _MAX_JSON_BYTES:
        raise ExternalAdapterError(adapter_id, "oversized_response", retryable=False)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalAdapterError(adapter_id, "invalid_json", retryable=False) from exc
    if not isinstance(payload, dict):
        raise ExternalAdapterError(adapter_id, "invalid_json", retryable=False)
    return payload


async def download_bounded_text(
    client: httpx.AsyncClient,
    adapter_id: str,
    url: str,
    *,
    max_bytes: int,
) -> str:
    try:
        async with client.stream("GET", url) as response:
            _raise_for_status(response, adapter_id)
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise ExternalAdapterError(
                    adapter_id, "oversized_result", retryable=False
                )
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise ExternalAdapterError(
                        adapter_id, "oversized_result", retryable=False
                    )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ExternalAdapterError(adapter_id, "network_error", retryable=True) from exc
    try:
        return bytes(content).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExternalAdapterError(adapter_id, "invalid_result_encoding", retryable=False) from exc
