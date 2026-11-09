"""Bearer check for the streamable HTTP endpoint.

The middleware is raw ASGI and does not read the body, so SSE responses still
stream. ``/health`` stays open for container probes.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

EXEMPT_PATHS = frozenset({"/health", "/health/"})


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str, *, exempt: Iterable[str] = EXEMPT_PATHS) -> None:
        if not token:
            raise ValueError("MCP_AUTH_TOKEN is empty.")
        self.app = app
        self._token = token
        self._exempt = frozenset(exempt)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in self._exempt:
            await self.app(scope, receive, send)
            return
        provided = _bearer_token(scope)
        if provided is None or not secrets.compare_digest(provided, self._token):
            await _unauthorized(send)
            return
        await self.app(scope, receive, send)


def _bearer_token(scope: Scope) -> str | None:
    header = Headers(scope=scope).get("authorization")
    if not header:
        return None
    scheme, _, rest = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = rest.strip()
    return token or None


async def _unauthorized(send: Send) -> None:
    body = json.dumps(
        {"error": "invalid_token", "error_description": "Authentication required"}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b'Bearer error="invalid_token"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
