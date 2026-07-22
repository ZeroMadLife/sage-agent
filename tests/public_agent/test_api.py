"""Standalone public Agent API, rate limit, and isolation coverage."""

from collections.abc import Sequence
from pathlib import Path

from fastapi.testclient import TestClient

from public_agent.app import create_public_agent_app
from public_agent.corpus import PublicDocument
from public_agent.limiter import SlidingWindowRateLimiter
from public_agent.model import PublicModelAnswer


class StubModel:
    async def answer(
        self,
        question: str,
        evidence: Sequence[PublicDocument],
    ) -> PublicModelAnswer:
        return PublicModelAnswer("公开回答 [E1]", input_tokens=50, output_tokens=8)


def _app(*, requests: int = 12):
    return create_public_agent_app(
        package_path=Path("data/public/sage-public-v1.json"),
        model=StubModel(),
        limiter=SlidingWindowRateLimiter(requests=requests, window_seconds=60),
    )


def test_public_api_returns_no_store_citations_and_package_receipt() -> None:
    response = TestClient(_app()).post(
        "/api/public/v1/ask",
        json={"question": "Harness 如何恢复运行？"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-public-package-revision"] == "2026-07-22.1"
    body = response.json()
    assert body["status"] == "answered"
    assert body["citations"][0]["document_id"] == "harness-2"
    assert body["receipt"]["evidence_ids"] == ["harness-2"]
    assert body["usage"] == {"input_tokens": 50, "output_tokens": 8}
    assert all(term not in response.text for term in ("/Users/", "workspace_root", "api_key"))


def test_public_api_limits_by_socket_client_without_trusting_forwarded_header() -> None:
    client = TestClient(_app(requests=1))
    headers = {"X-Forwarded-For": "198.51.100.1"}

    first = client.post(
        "/api/public/v1/ask",
        json={"question": "Sage 是什么？"},
        headers=headers,
    )
    limited = client.post(
        "/api/public/v1/ask",
        json={"question": "Knowledge 是什么？"},
        headers={"X-Forwarded-For": "203.0.113.5"},
    )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["cache-control"] == "no-store"
    assert limited.headers["x-public-package-revision"] == "2026-07-22.1"
    assert limited.headers["retry-after"] == "60"
    assert limited.json() == {"detail": "public Agent rate limit exceeded"}


def test_public_api_has_no_private_routes_or_schema_browser() -> None:
    client = TestClient(_app())

    missing = client.get("/docs")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    assert missing.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/v1/coding/models").status_code == 404
    assert client.get("/api/v1/knowledge").status_code == 404


def test_public_health_reports_package_and_configuration_state() -> None:
    response = TestClient(_app()).get("/healthz")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "ready"
    assert response.json()["package_revision"] == "2026-07-22.1"


def test_unconfigured_public_agent_fails_closed() -> None:
    app = create_public_agent_app(
        package_path=Path("data/public/sage-public-v1.json"),
        model=None,
    )
    client = TestClient(app)

    assert client.get("/healthz").json()["status"] == "not_configured"
    response = client.post("/api/public/v1/ask", json={"question": "Sage 是什么？"})
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-public-package-revision"] == "2026-07-22.1"
    assert response.json() == {"detail": "public Agent is not configured"}
