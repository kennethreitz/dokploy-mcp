import json

import httpx
import pytest
import respx

from dokploy_mcp.client import DokployClient, normalize_base_url
from dokploy_mcp.errors import DokployError

BASE = "https://dokploy.example.com/api/"


def test_normalize_base_url_appends_api_once() -> None:
    assert normalize_base_url("https://dokploy.example.com") == BASE
    assert normalize_base_url("https://dokploy.example.com/") == BASE
    assert normalize_base_url("https://dokploy.example.com/api") == BASE
    assert normalize_base_url("https://dokploy.example.com/panel") == "https://dokploy.example.com/panel/api/"
    with pytest.raises(DokployError):
        normalize_base_url("dokploy.example.com")


@respx.mock
def test_sends_api_key_and_joins_api_prefix() -> None:
    route = respx.get(f"{BASE}project.all").mock(return_value=httpx.Response(200, json=[]))
    with DokployClient("https://dokploy.example.com", "secret-key") as client:
        assert client.get("project.all") == []
    request = route.calls[0].request
    assert request.headers["x-api-key"] == "secret-key"
    assert str(request.url) == f"{BASE}project.all"


@respx.mock
def test_post_omits_null_fields_and_surfaces_api_errors() -> None:
    route = respx.post(f"{BASE}application.deploy").mock(
        return_value=httpx.Response(200, json={"deploymentId": "dep_1"})
    )
    with DokployClient("https://dokploy.example.com/api", "secret-key") as client:
        result = client.post("application.deploy", body={"applicationId": "app_1", "title": None})
    assert result == {"deploymentId": "dep_1"}
    assert json.loads(route.calls[0].request.read()) == {"applicationId": "app_1"}

    respx.post(f"{BASE}application.start").mock(
        return_value=httpx.Response(
            400,
            json={"code": "BAD_REQUEST", "message": "Application not found", "issues": [{"message": "missing"}]},
        )
    )
    with DokployClient("https://dokploy.example.com", "secret-key") as client:
        with pytest.raises(DokployError, match="Application not found") as exc:
            client.post("application.start", body={"applicationId": "missing"})
    assert exc.value.status_code == 400


@respx.mock
def test_redirect_tells_you_to_fix_the_url() -> None:
    respx.get("http://dokploy.example.com/api/project.all").mock(
        return_value=httpx.Response(301, headers={"location": "https://dokploy.example.com/api/project.all"})
    )
    with DokployClient("http://dokploy.example.com", "secret-key") as client:
        with pytest.raises(DokployError, match="DOKPLOY_URL"):
            client.get("project.all")
