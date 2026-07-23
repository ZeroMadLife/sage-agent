"""Provider URL and probe security tests."""

import socket

import httpx
import pytest

from core.cloud.model_providers import (
    ProviderDestination,
    ProviderPinnedTransport,
    ProviderProbe,
    ProviderProbeError,
    RuntimeProviderCredential,
    validate_provider_base_url,
)


def _credential(*, mode: str = "openai_chat_completions") -> RuntimeProviderCredential:
    return RuntimeProviderCredential(
        provider_id="provider-a",
        api_mode=mode,  # type: ignore[arg-type]
        base_url="https://api.example.com/v1",
        api_key="top-secret-key",
        models=(),
    )


def _public_resolver(*_args: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://127.0.0.1/v1",
        "https://10.0.0.2/v1",
        "https://metadata.internal/v1",
        "https://user:password@example.com/v1",
    ],
)
def test_cloud_base_url_rejects_private_and_credential_destinations(url: str) -> None:
    with pytest.raises(ValueError):
        validate_provider_base_url(url, app_env="production")


def test_development_only_allows_loopback_http() -> None:
    assert (
        validate_provider_base_url("http://localhost:11434/v1", app_env="development")
        == "http://localhost:11434/v1"
    )
    with pytest.raises(ValueError, match="HTTPS"):
        validate_provider_base_url("http://provider.example/v1", app_env="development")


async def test_probe_uses_auth_header_without_leaking_upstream_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer top-secret-key"
        return httpx.Response(401, text="invalid top-secret-key")

    probe = ProviderProbe(
        app_env="production",
        resolver=_public_resolver,
        client_factory=lambda _destination: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    with pytest.raises(ProviderProbeError) as error:
        await probe.test(_credential())

    assert str(error.value) == "Provider authentication failed"
    assert "top-secret-key" not in str(error.value)


async def test_probe_discovers_bounded_openai_and_anthropic_model_catalogs() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})

    probe = ProviderProbe(
        app_env="production",
        resolver=_public_resolver,
        client_factory=lambda _destination: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    openai_models = await probe.discover(_credential())
    anthropic_models = await probe.discover(_credential(mode="anthropic_messages"))

    assert openai_models == ["model-a", "model-b"]
    assert anthropic_models == ["model-a", "model-b"]
    assert seen[0].url.path == "/v1/models"
    assert seen[1].url.path == "/v1/models"
    assert seen[1].headers["x-api-key"] == "top-secret-key"


async def test_probe_rejects_dns_resolution_to_private_address_before_http() -> None:
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"data": []})

    def private_resolver(*_args: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]

    probe = ProviderProbe(
        app_env="production",
        resolver=private_resolver,
        client_factory=lambda _destination: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    with pytest.raises(ProviderProbeError, match="not allowed"):
        await probe.test(_credential())

    assert called is False


async def test_pinned_transport_connects_to_validated_ip_with_original_host_and_sni() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    destination = ProviderDestination(
        base_url="https://api.example.com/v1",
        hostname="api.example.com",
        addresses=("93.184.216.34",),
    )
    transport = ProviderPinnedTransport(
        destination,
        inner=httpx.MockTransport(handler),
    )
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        response = await client.get("https://api.example.com/v1/models")

    assert response.status_code == 200
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "api.example.com"
    assert seen[0].extensions["sni_hostname"] == "api.example.com"


def test_runtime_credential_repr_never_contains_api_key() -> None:
    credential = _credential()

    assert "top-secret-key" not in repr(credential)
