"""stdio for local clients, streamable HTTP for a hosted assistant."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from dokploy_mcp.auth import BearerAuthMiddleware
from dokploy_mcp.server import FastMCP, create_server

DEFAULT_PORT = 8765
_LOCAL_HOSTS = ("127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*")
_LOCAL_ORIGINS = (
    "http://127.0.0.1",
    "http://127.0.0.1:*",
    "http://localhost",
    "http://localhost:*",
    "http://[::1]",
    "http://[::1]:*",
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dokploy-mcp", description="MCP server for a Dokploy instance.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("stdio", help="Speak MCP on stdin/stdout. This is the default.")
    http = sub.add_parser("http", help="Serve streamable HTTP on /mcp.")
    http.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    http.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    command = args.command or "stdio"

    _require_dokploy_env()
    server = create_server()
    if command == "http":
        port = args.port if args.port is not None else _port()
        token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if not token:
            _die("MCP_AUTH_TOKEN is required for HTTP mode. It is the bearer token clients send.")
        app = build_http_app(server, host=args.host, auth_token=token)
        uvicorn.run(
            app,
            host=args.host,
            port=port,
            proxy_headers=True,
            forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"),
            log_level=os.environ.get("MCP_LOG_LEVEL", "info"),
        )
        return
    server.run(transport="stdio")


def build_http_app(server: FastMCP, *, host: str, auth_token: str) -> BearerAuthMiddleware:
    """ASGI app: /mcp speaks MCP, /health is open, everything else needs the bearer token."""
    app = server.streamable_http_app(
        transport_security=transport_security(host),
        stateless_http=True,
        json_response=True,
    )
    return BearerAuthMiddleware(app, auth_token)


def transport_security(host: str) -> TransportSecuritySettings:
    """Allow the Host header this process actually serves.

    Binding 0.0.0.0 is the container case, behind Dokploy's proxy, so DNS-rebinding
    protection is off unless MCP_ALLOWED_HOSTS is set. A localhost bind keeps the
    allowlist. Set MCP_ALLOWED_HOSTS (and MCP_ALLOWED_ORIGINS for browsers) to
    turn the check on for a public hostname.
    """
    allowed = _csv("MCP_ALLOWED_HOSTS")
    if allowed:
        hosts: list[str] = []
        for item in allowed:
            hosts.append(item)
            if ":" not in item and not item.endswith(":*"):
                hosts.append(f"{item}:*")
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=_csv("MCP_ALLOWED_ORIGINS"),
        )
    if host.strip() in {"127.0.0.1", "localhost", "::1"}:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(_LOCAL_HOSTS),
            allowed_origins=list(_LOCAL_ORIGINS),
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _require_dokploy_env() -> None:
    missing = [name for name in ("DOKPLOY_URL", "DOKPLOY_API_KEY") if not os.environ.get(name, "").strip()]
    if missing:
        _die(f"Set {', '.join(missing)} before starting dokploy-mcp.")


def _port() -> int:
    raw = os.environ.get("MCP_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw)
    except ValueError:
        _die(f"MCP_PORT must be an integer, got {raw!r}.")
    if not 1 <= port <= 65535:
        _die(f"MCP_PORT must be between 1 and 65535, got {port}.")
    return port


def _csv(name: str) -> list[str]:
    return [part.strip() for part in os.environ.get(name, "").split(",") if part.strip()]


def _die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
