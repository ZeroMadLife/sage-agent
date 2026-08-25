"""Small dependency-free JWT and refresh-token primitives for Sage clients."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass


class InvalidAccessToken(ValueError):
    """The presented access token is malformed, unsigned, or expired."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Validated claims needed to resolve one API request."""

    user_id: str
    session_id: str
    expires_at: int


def new_refresh_token() -> str:
    """Generate a high-entropy opaque refresh token."""
    return secrets.token_urlsafe(48)


def encode_access_token(*, user_id: str, session_id: str, secret: str, ttl_seconds: int) -> str:
    """Create a short-lived HS256 JWT without exposing a signing dependency."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "sage",
        "sub": user_id,
        "sid": session_id,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": secrets.token_urlsafe(18),
    }
    encoded_header = _encode_json(header)
    encoded_payload = _encode_json(payload)
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64encode(signature)}"


def decode_access_token(token: str, *, secret: str) -> AccessTokenClaims:
    """Verify a JWT's algorithm, issuer, signature, and time bounds."""
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".", 2)
        header = json.loads(_b64decode(encoded_header))
        payload = json.loads(_b64decode(encoded_payload))
        if header != {"alg": "HS256", "typ": "JWT"}:
            raise InvalidAccessToken("unsupported access token header")
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        supplied = _b64decode(encoded_signature)
        if not hmac.compare_digest(expected, supplied):
            raise InvalidAccessToken("invalid access token signature")
        user_id = str(payload["sub"])
        session_id = str(payload["sid"])
        expires_at = int(payload["exp"])
        if payload.get("iss") != "sage" or not user_id or not session_id:
            raise InvalidAccessToken("invalid access token claims")
        if expires_at <= int(time.time()):
            raise InvalidAccessToken("access token expired")
    except (
        InvalidAccessToken,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        if isinstance(exc, InvalidAccessToken):
            raise
        raise InvalidAccessToken("invalid access token") from exc
    return AccessTokenClaims(user_id=user_id, session_id=session_id, expires_at=expires_at)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _encode_json(value: Mapping[str, object]) -> str:
    return _b64encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))
