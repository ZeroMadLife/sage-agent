"""Invite-only GitHub OAuth with PKCE and one-time server state."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from core.cloud.auth.models import CloudUser
from core.cloud.auth.repository import CloudRepository
from core.cloud.security import SecretCipher, StateSigner

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"
_EMAILS_URL = "https://api.github.com/user/emails"
_TRANSACTION_TTL = timedelta(minutes=5)
_MAX_PROVIDER_RESPONSE_BYTES = 1_000_000


class InvalidOAuthTransaction(Exception):
    """The browser OAuth transaction is missing, expired, or replayed."""


class OAuthRegistrationDenied(Exception):
    """A new identity did not present a matching one-time invite."""


class OAuthProviderError(Exception):
    """GitHub returned an unusable response without exposing its body."""


@dataclass(frozen=True, slots=True)
class GitHubOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scope: str
    transaction_secret: str
    token_encryption_secret: str

    def __post_init__(self) -> None:
        if not self.client_id or not self.client_secret:
            raise ValueError("GitHub OAuth client credentials are required")
        if not self.redirect_uri.startswith(("http://localhost:", "https://")):
            raise ValueError("GitHub OAuth redirect URI must use HTTPS or localhost")


@dataclass(frozen=True, slots=True)
class OAuthStart:
    authorization_url: str
    browser_binding: str


@dataclass(frozen=True, slots=True)
class OAuthCompletion:
    user: CloudUser
    return_to: str


class GitHubOAuthService:
    """Coordinate GitHub identity without exposing provider tokens to clients."""

    def __init__(
        self,
        repository: CloudRepository,
        config: GitHubOAuthConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._repository = repository
        self._config = config
        self._state_signer = StateSigner(config.transaction_secret)
        self._transaction_cipher = SecretCipher(config.transaction_secret)
        self._token_cipher = SecretCipher(config.token_encryption_secret)
        self._http_client = http_client

    async def start(self, *, invite_code: str | None, return_to: str) -> OAuthStart:
        """Create a five-minute transaction and a PKCE authorization URL."""
        state = self._state_signer.issue()
        browser_binding = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
        payload = self._transaction_cipher.encrypt(
            json.dumps(
                {
                    "code_verifier": verifier,
                    "invite_code": invite_code,
                    "return_to": return_to,
                },
                separators=(",", ":"),
            ),
            purpose="github-oauth-transaction",
        )
        await self._repository.create_oauth_transaction(
            provider="github",
            state=state,
            browser_binding=browser_binding,
            encrypted_payload=payload,
            expires_at=datetime.now(UTC) + _TRANSACTION_TTL,
        )
        query = urlencode(
            {
                "client_id": self._config.client_id,
                "redirect_uri": self._config.redirect_uri,
                "scope": self._config.scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return OAuthStart(
            authorization_url=f"{_AUTHORIZE_URL}?{query}",
            browser_binding=browser_binding,
        )

    async def complete(self, *, code: str, state: str, browser_binding: str) -> OAuthCompletion:
        """Consume state, exchange the code, and bind the GitHub identity."""
        if not code or not browser_binding or not self._state_signer.verify(state):
            raise InvalidOAuthTransaction
        try:
            encrypted_payload = await self._repository.consume_oauth_transaction(
                provider="github", state=state, browser_binding=browser_binding
            )
            payload = json.loads(
                self._transaction_cipher.decrypt(
                    encrypted_payload, purpose="github-oauth-transaction"
                )
            )
            verifier = str(payload["code_verifier"])
            return_to = str(payload["return_to"])
            invite_code_value = payload.get("invite_code")
            invite_code = str(invite_code_value) if invite_code_value else None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, PermissionError) as exc:
            raise InvalidOAuthTransaction from exc

        access_token, scopes = await self._exchange_code(code=code, verifier=verifier)
        profile = await self._github_json(_USER_URL, access_token)
        provider_subject = str(profile.get("id", ""))
        provider_login = str(profile.get("login", ""))
        if not provider_subject or not provider_login:
            raise OAuthProviderError
        email = await self._verified_email(profile, access_token)
        display_name = str(profile.get("name") or provider_login)
        try:
            user = await self._repository.get_or_create_identity(
                provider="github",
                provider_subject=provider_subject,
                email=email,
                display_name=display_name,
                invite_code=invite_code,
            )
        except PermissionError as exc:
            raise OAuthRegistrationDenied from exc

        encrypted_token = self._token_cipher.encrypt(
            access_token, purpose=f"github-access-token:{user.user_id}"
        )
        await self._repository.upsert_provider_credential(
            user_id=user.user_id,
            provider="github",
            encrypted_access_token=encrypted_token,
            scopes=scopes,
            provider_login=provider_login,
        )
        return OAuthCompletion(user=user, return_to=return_to)

    async def _exchange_code(self, *, code: str, verifier: str) -> tuple[str, str]:
        response = await self._request(
            "POST",
            _TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "code": code,
                "redirect_uri": self._config.redirect_uri,
                "code_verifier": verifier,
            },
        )
        payload = _response_json(response)
        token = payload.get("access_token")
        if response.status_code != 200 or not isinstance(token, str) or not token:
            raise OAuthProviderError
        return token, str(payload.get("scope", ""))

    async def _github_json(self, url: str, access_token: str) -> dict[str, Any]:
        response = await self._request(
            "GET",
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        payload = _response_json(response)
        if response.status_code != 200 or not isinstance(payload, dict):
            raise OAuthProviderError
        return payload

    async def _verified_email(self, profile: dict[str, Any], access_token: str) -> str:
        response = await self._request(
            "GET",
            _EMAILS_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        payload = _response_json(response)
        if response.status_code != 200 or not isinstance(payload, list):
            raise OAuthProviderError
        verified = [
            item
            for item in payload
            if isinstance(item, dict)
            and item.get("verified") is True
            and isinstance(item.get("email"), str)
        ]
        primary = next((item for item in verified if item.get("primary") is True), None)
        selected = primary or (verified[0] if verified else None)
        if selected is None:
            raise OAuthProviderError
        return str(selected["email"]).strip().lower()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self._http_client is not None:
                response = await self._http_client.request(
                    method, url, follow_redirects=False, timeout=10.0, **kwargs
                )
            else:
                async with httpx.AsyncClient(follow_redirects=False, timeout=10.0) as client:
                    response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise OAuthProviderError from exc
        if len(response.content) > _MAX_PROVIDER_RESPONSE_BYTES:
            raise OAuthProviderError
        return response


def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OAuthProviderError from exc


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
