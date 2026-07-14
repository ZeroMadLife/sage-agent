"""SSRF-safe Provider URL validation and bounded model discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.cloud.model_providers.models import RuntimeProviderCredential

_MAX_DISCOVERED_MODELS = 500
_Resolver = Callable[..., Sequence[tuple[Any, ...]]]


class ProviderProbeError(RuntimeError):
    """A user-readable Provider error that never contains upstream secrets."""


def validate_provider_base_url(value: str, *, app_env: str) -> str:
    """Normalize a Provider base URL and reject obvious SSRF destinations."""
    normalized = value.strip().rstrip("/")
    if len(normalized) > 500:
        raise ValueError("Provider Base URL is too long")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Provider Base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Provider Base URL cannot include credentials, query, or fragment")
    hostname = parsed.hostname.lower().rstrip(".")
    loopback = _is_loopback_hostname(hostname)
    if parsed.scheme != "https" and not (app_env == "development" and loopback):
        raise ValueError("Provider Base URL must use HTTPS")
    if _is_forbidden_hostname(hostname) and not (app_env == "development" and loopback):
        raise ValueError("Provider Base URL cannot target a private network")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


async def assert_provider_destination_allowed(
    value: str,
    *,
    app_env: str,
    resolver: _Resolver = socket.getaddrinfo,
) -> str:
    """Resolve a Provider host and reject private or metadata destinations."""
    normalized = validate_provider_base_url(value, app_env=app_env)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname or ""
    if app_env == "development" and _is_loopback_hostname(hostname):
        return normalized
    try:
        addresses = await asyncio.to_thread(
            resolver,
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            0,
            socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ProviderProbeError("Provider host could not be resolved") from exc
    resolved = {str(item[4][0]).split("%", 1)[0] for item in addresses if len(item) > 4}
    if not resolved:
        raise ProviderProbeError("Provider host could not be resolved")
    if any(_is_forbidden_ip(address) for address in resolved):
        raise ProviderProbeError("Provider destination is not allowed")
    return normalized


class ProviderProbe:
    """Perform credential tests and model discovery without exposing raw failures."""

    def __init__(
        self,
        *,
        app_env: str,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        resolver: _Resolver = socket.getaddrinfo,
    ) -> None:
        self._app_env = app_env
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=10.0, follow_redirects=False)
        )
        self._resolver = resolver

    async def test(self, credential: RuntimeProviderCredential) -> None:
        await self.discover(credential, limit=1)

    async def discover(
        self, credential: RuntimeProviderCredential, *, limit: int = _MAX_DISCOVERED_MODELS
    ) -> list[str]:
        base_url = await assert_provider_destination_allowed(
            credential.base_url,
            app_env=self._app_env,
            resolver=self._resolver,
        )
        url = _models_url(base_url, credential.api_mode)
        headers = _auth_headers(credential)
        try:
            async with self._client_factory() as client:
                response = await client.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderProbeError("Provider connection failed") from exc
        if response.status_code in {401, 403}:
            raise ProviderProbeError("Provider authentication failed")
        if response.status_code == 429:
            raise ProviderProbeError("Provider rate limit reached")
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderProbeError(f"Provider returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderProbeError("Provider returned an invalid model catalog") from exc
        raw_models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            raise ProviderProbeError("Provider model discovery is not supported")
        models: list[str] = []
        for item in raw_models[: min(limit, _MAX_DISCOVERED_MODELS)]:
            model_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(model_id, str) and model_id.strip():
                models.append(model_id.strip())
        return models


def _models_url(base_url: str, api_mode: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if api_mode == "anthropic_messages" and not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/models", "", ""))


def _auth_headers(credential: RuntimeProviderCredential) -> dict[str, str]:
    if credential.api_mode == "anthropic_messages":
        return {
            "x-api-key": credential.api_key,
            "anthropic-version": "2023-06-01",
            "accept": "application/json",
        }
    return {"authorization": f"Bearer {credential.api_key}", "accept": "application/json"}


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_forbidden_hostname(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        return _is_forbidden_ip(hostname)
    except ValueError:
        return False


def _is_forbidden_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not address.is_global
