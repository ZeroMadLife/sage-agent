"""In-memory bootstrap and loopback request guard for the desktop profile."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

_BOOTSTRAP_FIELDS = {"instance_id", "nonce", "bearer", "origin", "data_dir"}
_MAX_BOOTSTRAP_BYTES = 64 * 1024


@dataclass(frozen=True)
class DesktopBootstrap:
    """Secrets and process identity delivered once through child stdin."""

    instance_id: str
    nonce: str
    bearer: str
    origin: str
    data_dir: Path

    @classmethod
    def from_json(cls, raw: str) -> DesktopBootstrap:
        try:
            if len(raw.encode("utf-8")) > _MAX_BOOTSTRAP_BYTES:
                raise ValueError
            payload: Any = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != _BOOTSTRAP_FIELDS:
                raise ValueError
            values = {name: payload[name] for name in _BOOTSTRAP_FIELDS}
            if not all(isinstance(value, str) and value for value in values.values()):
                raise ValueError
            if values["origin"] != "tauri://localhost":
                raise ValueError
            data_dir = Path(values["data_dir"])
            if not data_dir.is_absolute():
                raise ValueError
        except (KeyError, TypeError, UnicodeEncodeError, json.JSONDecodeError, ValueError):
            raise ValueError("invalid desktop bootstrap") from None
        return cls(
            instance_id=values["instance_id"],
            nonce=values["nonce"],
            bearer=values["bearer"],
            origin=values["origin"],
            data_dir=data_dir,
        )


@dataclass(frozen=True)
class DesktopSecurity:
    """Expected loopback request identity, retained in process memory only."""

    bearer: str
    origin: str
    host: str

    def reject_reason(self, *, authorization: str | None, host: str | None, origin: str | None) -> str | None:
        supplied = "" if authorization is None else authorization
        expected = f"Bearer {self.bearer}"
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return "desktop_bearer_rejected"
        if host != self.host:
            return "desktop_host_rejected"
        if origin != self.origin:
            return "desktop_origin_rejected"
        return None

    def websocket_reject_reason(
        self,
        *,
        host: str | None,
        origin: str | None,
        subprotocols: list[str],
    ) -> str | None:
        bearer_protocol = f"sage-bearer.{self.bearer}"
        supplied = next(
            (protocol for protocol in subprotocols if protocol.startswith("sage-bearer.")),
            "",
        )
        if not hmac.compare_digest(supplied.encode(), bearer_protocol.encode()):
            return "desktop_bearer_rejected"
        if host != self.host:
            return "desktop_host_rejected"
        if origin != self.origin:
            return "desktop_origin_rejected"
        if "sage.v1" not in subprotocols:
            return "desktop_protocol_rejected"
        return None


class DesktopSecurityMiddleware(BaseHTTPMiddleware):
    """Apply the same fail-closed identity gate to every HTTP/SSE request."""

    def __init__(self, app: Any, *, security: DesktopSecurity) -> None:
        super().__init__(app)
        self._security = security

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        reason = self._security.reject_reason(
            authorization=request.headers.get("authorization"),
            host=request.headers.get("host"),
            origin=request.headers.get("origin"),
        )
        if reason is not None:
            return JSONResponse(
                {"reason_code": reason, "action": "restart_sage"},
                status_code=403,
            )
        return await call_next(request)


__all__ = ["DesktopBootstrap", "DesktopSecurity", "DesktopSecurityMiddleware"]
