"""SSRF-safe Provider URL validation and bounded model discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.cloud.model_providers.models import ProviderDestination, RuntimeProviderCredential

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


async def resolve_provider_destination(
    value: str,
    *,
    app_env: str,
    resolver: _Resolver = socket.getaddrinfo,
) -> ProviderDestination:
    """Resolve a Provider host and reject private or metadata destinations."""
    normalized = validate_provider_base_url(value, app_env=app_env)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname or ""
    if app_env == "development" and _is_loopback_hostname(hostname):
        addresses = await _resolve_addresses(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            resolver,
        )
        if not addresses:
            addresses = (hostname,)
        return ProviderDestination(normalized, hostname, addresses)
    addresses = await _resolve_addresses(
        hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        resolver,
    )
    if not addresses:
        raise ProviderProbeError("Provider host could not be resolved")
    if any(_is_forbidden_ip(address) for address in addresses):
        raise ProviderProbeError("Provider destination is not allowed")
    return ProviderDestination(normalized, hostname, addresses)


async def assert_provider_destination_allowed(
    value: str,
    *,
    app_env: str,
    resolver: _Resolver = socket.getaddrinfo,
) -> str:
    destination = await resolve_provider_destination(
        value, app_env=app_env, resolver=resolver
    )
    return destination.base_url


async def _resolve_addresses(
    hostname: str,
    port: int,
    resolver: _Resolver,
) -> tuple[str, ...]:
    try:
        addresses = await asyncio.to_thread(
            resolver,
            hostname,
            port,
            0,
            socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ProviderProbeError("Provider host could not be resolved") from exc
    return tuple(
        sorted(
            {
                str(item[4][0]).split("%", 1)[0]
                for item in addresses
                if len(item) > 4
            }
        )
    )


class ProviderPinnedTransport(httpx.AsyncBaseTransport):
    """Bind an allowed hostname to its validated address for the real TCP request."""

    def __init__(
        self,
        destination: ProviderDestination,
        *,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._destination = destination
        self._inner = inner or httpx.AsyncHTTPTransport(trust_env=False, retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        hostname = (request.url.host or "").lower().rstrip(".")
        if hostname != self._destination.hostname:
            raise httpx.UnsupportedProtocol("Provider request host changed after validation")
        address = self._destination.addresses[0]
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=address),
            headers={**dict(request.headers), "host": request.url.netloc.decode("ascii")},
            stream=request.stream,
            extensions={
                **request.extensions,
                "sni_hostname": self._destination.hostname,
            },
        )
        return await self._inner.handle_async_request(pinned)

    async def aclose(self) -> None:
        await self._inner.aclose()


def create_provider_http_client(
    destination: ProviderDestination,
    *,
    timeout: float = 30.0,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ProviderPinnedTransport(destination),
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


class ProviderProbe:
    """Perform credential tests and model discovery without exposing raw failures."""

    def __init__(
        self,
        *,
        app_env: str,
        client_factory: Callable[[ProviderDestination], httpx.AsyncClient] | None = None,
        resolver: _Resolver = socket.getaddrinfo,
    ) -> None:
        self._app_env = app_env
        self._client_factory = client_factory or (
            lambda destination: create_provider_http_client(destination, timeout=10.0)
        )
        self._resolver = resolver

    async def test(self, credential: RuntimeProviderCredential) -> None:
        await self.discover(credential, limit=1)

    async def pin(
        self, credential: RuntimeProviderCredential
    ) -> RuntimeProviderCredential:
        destination = await resolve_provider_destination(
            credential.base_url,
            app_env=self._app_env,
            resolver=self._resolver,
        )
        return replace(credential, destination=destination)

    async def discover(
        self, credential: RuntimeProviderCredential, *, limit: int = _MAX_DISCOVERED_MODELS
    ) -> list[str]:
        pinned = await self.pin(credential)
        if pinned.destination is None:  # pragma: no cover - pin always sets it
            raise ProviderProbeError("Provider destination is not available")
        url = _models_url(pinned.destination.base_url, pinned.api_mode)
        headers = _auth_headers(credential)
        try:
            async with self._client_factory(pinned.destination) as client:
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
