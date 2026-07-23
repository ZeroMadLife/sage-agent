"""Standalone public Agent API, rate limit, and isolation coverage."""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from public_agent.app import create_public_agent_app
from public_agent.budget import DailyTokenBudget
from public_agent.client_identity import PublicClientIdentityResolver
from public_agent.corpus import PublicDocument
from public_agent.limiter import SlidingWindowRateLimiter
from public_agent.model import PublicModelAnswer
from public_agent.registry import PublishedPackageRegistry


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


def test_public_api_explicitly_refuses_chinese_private_session_and_memory_request() -> None:
    response = TestClient(_app()).post(
        "/api/public/v1/ask",
        json={"question": "请读取我的私人会话和长期记忆。"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "refused"
    assert body["citations"] == []
    assert body["receipt"]["evidence_ids"] == []
    assert body["usage"] == {"input_tokens": 0, "output_tokens": 0}


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


async def test_trusted_proxy_header_separates_clients_but_untrusted_peers_cannot_forge_it() -> None:
    resolver = PublicClientIdentityResolver.from_cidrs("172.16.0.0/12")
    app = create_public_agent_app(
        package_path=Path("data/public/sage-public-v1.json"),
        model=StubModel(),
        limiter=SlidingWindowRateLimiter(requests=1, window_seconds=60),
        client_identity=resolver,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("172.20.0.2", 50000)),
        base_url="http://testserver",
    ) as trusted:
        first = await trusted.post(
            "/api/public/v1/ask",
            json={"question": "Sage 是什么？"},
            headers={"X-Sage-Public-Client-IP": "198.51.100.1"},
        )
        second = await trusted.post(
            "/api/public/v1/ask",
            json={"question": "Knowledge 是什么？"},
            headers={"X-Sage-Public-Client-IP": "198.51.100.2"},
        )
    assert first.status_code == 200
    assert second.status_code == 200

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 50000)),
        base_url="http://testserver",
    ) as untrusted:
        assert (
            await untrusted.post(
                "/api/public/v1/ask",
                json={"question": "Sage 是什么？"},
                headers={"X-Sage-Public-Client-IP": "198.51.100.8"},
            )
        ).status_code == 200
        assert (
            await untrusted.post(
                "/api/public/v1/ask",
                json={"question": "Knowledge 是什么？"},
                headers={"X-Sage-Public-Client-IP": "198.51.100.9"},
            )
        ).status_code == 429


async def test_trusted_proxy_without_a_valid_internal_header_fails_closed() -> None:
    app = create_public_agent_app(
        package_path=Path("data/public/sage-public-v1.json"),
        model=StubModel(),
        client_identity=PublicClientIdentityResolver.from_cidrs("172.16.0.0/12"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("172.20.0.2", 50000)),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/api/public/v1/ask", json={"question": "Sage 是什么？"})

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid public proxy identity"}


def test_daily_token_budget_rejects_a_second_model_call_but_not_a_no_match() -> None:
    app = create_public_agent_app(
        package_path=Path("data/public/sage-public-v1.json"),
        model=StubModel(),
        token_budget=DailyTokenBudget(token_limit=100, reservation_tokens=100),
    )
    client = TestClient(app)

    assert client.post("/api/public/v1/ask", json={"question": "Sage 是什么？"}).status_code == 200
    no_match = client.post("/api/public/v1/ask", json={"question": "今天天气怎么样？"})
    limited = client.post("/api/public/v1/ask", json={"question": "Harness 如何恢复？"})

    assert no_match.status_code == 200
    assert no_match.json()["status"] == "no_match"
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "3600"
    assert limited.json() == {"detail": "public Agent usage budget exceeded"}


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


def test_public_api_follows_publish_and_revoke_without_process_restart(tmp_path: Path) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(Path("data/public/sage-public-v1.json"), actor="root")
    client = TestClient(create_public_agent_app(package_registry_root=tmp_path, model=StubModel()))

    before = client.post("/api/public/v1/ask", json={"question": "Sage 是什么？"})
    assert before.json()["receipt"]["package_revision"] == "2026-07-22.1"

    payload = json.loads(Path("data/public/sage-public-v1.json").read_text(encoding="utf-8"))
    payload["revision"] = "2026-07-23.1"
    content = payload["documents"][0]["content"] + " P3 发布验证"
    payload["documents"][0]["content"] = content
    payload["documents"][0]["content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    registry.stage_payload(payload, actor="sage-deploy")
    registry.activate(
        "sage-public",
        "2026-07-23.1",
        expected_active_revision="2026-07-22.1",
        actor="sage-deploy",
    )

    published = client.post("/api/public/v1/ask", json={"question": "Sage 是什么？"})
    assert published.headers["x-public-package-revision"] == "2026-07-23.1"
    assert published.json()["receipt"]["package_revision"] == "2026-07-23.1"

    registry.revoke(
        "sage-public",
        "2026-07-23.1",
        expected_active_revision="2026-07-23.1",
        actor="sage-deploy",
        reason="E2E 回退",
    )
    rolled_back = client.post("/api/public/v1/ask", json={"question": "Sage 是什么？"})
    assert rolled_back.headers["x-public-package-revision"] == "2026-07-22.1"
    assert rolled_back.json()["receipt"]["package_revision"] == "2026-07-22.1"


def test_public_api_reports_degraded_when_active_registry_is_invalid(tmp_path: Path) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(Path("data/public/sage-public-v1.json"), actor="root")
    active = tmp_path / "packages/sage-public/2026-07-22.1.json"
    active.write_text("{}", encoding="utf-8")
    client = TestClient(create_public_agent_app(package_registry_root=tmp_path, model=StubModel()))

    health = client.get("/healthz")
    response = client.post("/api/public/v1/ask", json={"question": "Sage 是什么？"})

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.headers["x-public-package-revision"] == "unavailable"
    assert response.status_code == 503
    assert response.json() == {"detail": "public package unavailable"}
