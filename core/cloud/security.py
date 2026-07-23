"""Small cryptographic primitives for cloud credentials and OAuth state."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretCipher:
    """Authenticated encryption derived from a deployment secret."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("encryption secret must contain at least 32 characters")
        self._cipher = AESGCM(hashlib.sha256(secret.encode("utf-8")).digest())

    def encrypt(self, plaintext: str, *, purpose: str) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), purpose.encode("utf-8"))
        return _encode(nonce + ciphertext)

    def decrypt(self, encoded: str, *, purpose: str) -> str:
        payload = _decode(encoded)
        if len(payload) < 13:
            raise ValueError("encrypted payload is malformed")
        plaintext = self._cipher.decrypt(payload[:12], payload[12:], purpose.encode("utf-8"))
        return plaintext.decode("utf-8")


class StateSigner:
    """Issue opaque OAuth state with an independently verifiable HMAC."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("OAuth transaction secret must contain at least 32 characters")
        self._secret = secret.encode("utf-8")

    def issue(self) -> str:
        nonce = secrets.token_urlsafe(32)
        signature = hmac.new(self._secret, nonce.encode("ascii"), hashlib.sha256).digest()
        return f"{nonce}.{_encode(signature)}"

    def verify(self, state: str) -> bool:
        try:
            nonce, signature = state.split(".", 1)
            supplied = _decode(signature)
        except (ValueError, UnicodeError):
            return False
        expected = hmac.new(self._secret, nonce.encode("ascii"), hashlib.sha256).digest()
        return hmac.compare_digest(supplied, expected)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
