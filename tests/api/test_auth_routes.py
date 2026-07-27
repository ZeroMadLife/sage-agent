"""Auth route tests."""

from fastapi.testclient import TestClient

from api.main import create_app
from core.auth import AuthManager


def test_auth_route_returns_user_id_for_valid_passphrase() -> None:
    """POST /api/v1/auth returns an opaque user ID for valid passphrases."""
    client = TestClient(create_app(auth=AuthManager("sage2026")))

    response = client.post("/api/v1/auth", json={"passphrase": "sage2026"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["user_id"].startswith("u_")


def test_auth_route_rejects_invalid_passphrase() -> None:
    """Invalid passphrases return valid=false without a user ID."""
    client = TestClient(create_app(auth=AuthManager("sage2026")))

    response = client.post("/api/v1/auth", json={"passphrase": "wrong"})

    assert response.status_code == 200
    assert response.json() == {"user_id": "", "valid": False}


def test_auth_route_allows_anonymous_when_auth_unconfigured() -> None:
    """If no auth manager is configured, the API stays usable in dev mode."""
    client = TestClient(create_app(auth=None))

    response = client.post("/api/v1/auth", json={"passphrase": "anything"})

    assert response.status_code == 200
    assert response.json() == {"user_id": "anonymous", "valid": True}
