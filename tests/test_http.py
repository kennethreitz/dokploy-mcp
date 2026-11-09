import socket
import subprocess
import sys
import time

import httpx
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from test_tools import EXPECTED_TOOLS


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_http_refuses_to_start_without_a_token() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "dokploy_mcp", "http", "--port", "9"],
        env={
            "DOKPLOY_URL": "https://dokploy.example.com",
            "DOKPLOY_API_KEY": "secret-key",
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "MCP_AUTH_TOKEN" in result.stderr


def test_stdio_refuses_to_start_without_dokploy_env() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "dokploy_mcp", "stdio"],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "DOKPLOY_URL" in result.stderr


@pytest.mark.anyio
async def test_http_server_starts_and_lists_tools() -> None:
    port = _free_port()
    token = "test-token"
    process = subprocess.Popen(
        [sys.executable, "-m", "dokploy_mcp", "http", "--host", "127.0.0.1", "--port", str(port)],
        env={
            "DOKPLOY_URL": "https://dokploy.example.com",
            "DOKPLOY_API_KEY": "secret-key",
            "MCP_AUTH_TOKEN": token,
            "MCP_LOG_LEVEL": "warning",
            "PATH": "/usr/bin:/bin",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_health(port, process)
        async with httpx.AsyncClient(timeout=5.0) as http:
            health = await http.get(f"http://127.0.0.1:{port}/health")
            denied = await http.post(f"http://127.0.0.1:{port}/mcp", json={"jsonrpc": "2.0", "method": "tools/list"})
            wrong = await http.post(
                f"http://127.0.0.1:{port}/mcp",
                headers={"Authorization": "Bearer wrong-token"},
                json={"jsonrpc": "2.0", "method": "tools/list"},
            )
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert denied.status_code == 401
        assert wrong.status_code == 401

        import httpx2

        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        ) as http_client:
            transport = streamable_http_client(
                f"http://127.0.0.1:{port}/mcp",
                http_client=http_client,
            )
            async with Client(transport, read_timeout_seconds=15) as client:
                listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert names == EXPECTED_TOOLS
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _wait_for_health(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            raise AssertionError(f"server exited {process.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
        except httpx.HTTPError as exc:
            last_error = str(exc)
        else:
            if response.status_code == 200:
                return
            last_error = f"status {response.status_code}"
        time.sleep(0.1)
    raise AssertionError(f"health check did not pass: {last_error}")
