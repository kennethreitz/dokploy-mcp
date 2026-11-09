"""HTTP client for Dokploy's REST API.

Dokploy exposes its tRPC procedures as ``/api/<router>.<procedure>``. Reads are
GET with query parameters. Writes are POST with a JSON body. The API key is the
``x-api-key`` header.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from dokploy_mcp import __version__
from dokploy_mcp.errors import DokployError

logger = logging.getLogger(__name__)

USER_AGENT = f"dokploy-mcp/{__version__}"


def normalize_base_url(url: str) -> str:
    """Return ``<origin>/api/`` for a panel URL or an URL that already ends in ``/api``."""
    raw = url.strip().rstrip("/")
    if not raw:
        raise DokployError("DOKPLOY_URL is empty.")
    if not raw.startswith(("http://", "https://")):
        raise DokployError("DOKPLOY_URL must start with http:// or https://.")
    if raw.endswith("/api"):
        return raw + "/"
    return raw + "/api/"


class DokployClient:
    """Sync client. One instance is safe to reuse across tool calls."""

    def __init__(self, url: str, api_key: str, *, timeout: httpx.Timeout | None = None) -> None:
        key = api_key.strip()
        if not key:
            raise DokployError("DOKPLOY_API_KEY is empty.")
        self.base_url = normalize_base_url(url)
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={
                "x-api-key": key,
                "accept": "application/json",
                "user-agent": USER_AGENT,
            },
            timeout=timeout or httpx.Timeout(30.0, read=120.0),
            follow_redirects=False,
        )

    @classmethod
    def from_env(cls) -> DokployClient:
        url = os.environ.get("DOKPLOY_URL", "")
        api_key = os.environ.get("DOKPLOY_API_KEY", "")
        if not url.strip() or not api_key.strip():
            raise DokployError("Set DOKPLOY_URL and DOKPLOY_API_KEY to your Dokploy panel and API key.")
        return cls(url, api_key)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DokployClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, procedure: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", procedure, params=params)

    def post(self, procedure: str, *, body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", procedure, body=body)

    def request(
        self,
        method: str,
        procedure: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        path = procedure.lstrip("/")
        query = _omit_none(params)
        payload = _omit_none(body) if body is not None else None
        response = self._http.request(method, path, params=query or None, json=payload)
        logger.info("Dokploy %s %s -> %s", method, path, response.status_code)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location", "")
            raise DokployError(
                f"Dokploy redirected {method} {path} to {location}. "
                "Set DOKPLOY_URL to the final https URL of the panel.",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise DokployError(
                _error_message(response, path),
                status_code=response.status_code,
            )
        return _decode(response)


def _omit_none(values: dict[str, Any] | None) -> dict[str, Any]:
    if not values:
        return {}
    return {key: value for key, value in values.items() if value is not None}


def _decode(response: httpx.Response) -> Any:
    if not response.content:
        return None
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return response.json()
    return response.text


def _error_message(response: httpx.Response, path: str) -> str:
    detail = response.text.strip()
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message") or detail or response.reason_phrase
        issues = payload.get("issues")
        if issues:
            return f"Dokploy {path} returned {response.status_code}: {message}. Issues: {issues}"
        return f"Dokploy {path} returned {response.status_code}: {message}"
    if detail:
        return f"Dokploy {path} returned {response.status_code}: {detail[:500]}"
    return f"Dokploy {path} returned {response.status_code}."
