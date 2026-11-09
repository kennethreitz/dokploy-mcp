import json

import httpx
import pytest
import respx
from mcp import Client

from dokploy_mcp.client import DokployClient
from dokploy_mcp.server import create_server

BASE = "https://dokploy.example.com/api/"

EXPECTED_TOOLS = {
    "list_projects",
    "get_project",
    "list_environments",
    "list_applications",
    "get_application",
    "list_compose_services",
    "get_compose",
    "list_databases",
    "get_database",
    "list_deployments",
    "list_deployment_queue",
    "read_deployment_logs",
    "read_application_logs",
    "read_compose_logs",
    "list_domains",
    "list_servers",
    "get_server",
    "get_server_health",
    "get_application_monitoring",
    "get_instance_status",
    "deploy_application",
    "redeploy_application",
    "start_application",
    "stop_application",
    "restart_application",
    "deploy_compose",
    "redeploy_compose",
    "start_compose",
    "stop_compose",
    "restart_compose",
    "update_application_env",
    "update_compose_env",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _payload(result: object) -> dict:
    content = result.structured_content  # type: ignore[attr-defined]
    assert isinstance(content, dict)
    if "result" in content and not any(key in content for key in ("projects", "applicationId", "composeId", "databases")):
        inner = content["result"]
        assert isinstance(inner, dict)
        return inner
    return content


@pytest.mark.anyio
async def test_server_lists_tools() -> None:
    server = create_server(client=DokployClient("https://dokploy.example.com", "k"))
    async with Client(server) as client:
        listed = await client.list_tools()
    names = {tool.name for tool in listed.tools}
    assert names == EXPECTED_TOOLS
    assert all(tool.description for tool in listed.tools)
    by_name = {tool.name: tool for tool in listed.tools}
    assert by_name["list_projects"].annotations.read_only_hint is True
    assert by_name["stop_application"].annotations.destructive_hint is True
    assert by_name["restart_compose"].annotations.destructive_hint is True
    assert by_name["deploy_application"].annotations.destructive_hint is False
    delete_tools = [name for name in names if "delete" in name or "remove" in name]
    assert delete_tools == []


@pytest.mark.anyio
async def test_list_projects_and_databases_use_the_api() -> None:
    with respx.mock:
        respx.get(f"{BASE}project.all").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "projectId": "proj_1",
                        "name": "Shop",
                        "environments": [
                            {
                                "environmentId": "env_1",
                                "name": "production",
                                "isDefault": True,
                                "applications": [
                                    {"applicationId": "app_1", "name": "web", "applicationStatus": "done"}
                                ],
                                "compose": [],
                                "postgres": [{"postgresId": "pg_1"}],
                            }
                        ],
                    }
                ],
            )
        )
        respx.get(f"{BASE}project.one").mock(
            return_value=httpx.Response(
                200,
                json={
                    "projectId": "proj_1",
                    "name": "Shop",
                    "environments": [
                        {
                            "environmentId": "env_1",
                            "name": "production",
                            "applications": [],
                            "postgres": [
                                {
                                    "postgresId": "pg_1",
                                    "name": "primary",
                                    "appName": "shop-db",
                                    "applicationStatus": "done",
                                    "databasePassword": "nope",
                                }
                            ],
                        }
                    ],
                },
            )
        )
        server = create_server(client=DokployClient("https://dokploy.example.com", "secret-key"))
        async with Client(server) as client:
            projects = _payload(await client.call_tool("list_projects", {}))
            databases = _payload(await client.call_tool("list_databases", {}))

    assert projects["projects"][0]["name"] == "Shop"
    assert projects["projects"][0]["environments"][0]["applicationCount"] == 1
    assert databases["databases"][0]["name"] == "primary"
    assert databases["databases"][0]["engine"] == "postgres"
    assert "databasePassword" not in databases["databases"][0]


@pytest.mark.anyio
async def test_deploy_and_env_update_and_compose_restart() -> None:
    with respx.mock:
        respx.get(f"{BASE}application.one").mock(
            return_value=httpx.Response(
                200,
                json={
                    "applicationId": "app_1",
                    "name": "web",
                    "appName": "shop-web",
                    "applicationStatus": "done",
                    "env": "PORT=3000\nSHARED=old\n",
                    "buildArgs": "NODE_ENV=production",
                    "buildSecrets": "NPM_TOKEN=keep",
                    "createEnvFile": True,
                },
            )
        )
        saved = respx.post(f"{BASE}application.saveEnvironment").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        deployed = respx.post(f"{BASE}application.deploy").mock(
            return_value=httpx.Response(200, json={"deploymentId": "dep_1"})
        )
        respx.post(f"{BASE}compose.stop").mock(return_value=httpx.Response(200, json={"stopped": True}))
        respx.post(f"{BASE}compose.start").mock(
            return_value=httpx.Response(400, json={"message": "port in use"})
        )
        server = create_server(client=DokployClient("https://dokploy.example.com", "secret-key"))
        async with Client(server) as client:
            updated = _payload(
                await client.call_tool(
                    "update_application_env",
                    {"application_id": "app_1", "variables": {"SHARED": "new"}, "unset": ["GONE"]},
                )
            )
            deployment = _payload(
                await client.call_tool(
                    "deploy_application",
                    {"application_id": "app_1", "title": "Ship it"},
                )
            )
            restart = await client.call_tool("restart_compose", {"compose_id": "cmp_1"})

    body = json.loads(saved.calls[0].request.read())
    assert body["env"] == "PORT=3000\nSHARED=new\n"
    assert body["buildArgs"] == "NODE_ENV=production"
    assert body["buildSecrets"] == "NPM_TOKEN=keep"
    assert body["createEnvFile"] is True
    assert updated["env"] == "PORT=3000\nSHARED=new\n"
    assert json.loads(deployed.calls[0].request.read()) == {
        "applicationId": "app_1",
        "title": "Ship it",
    }
    assert deployment["result"]["deploymentId"] == "dep_1"
    assert restart.is_error is True
    assert "left stopped" in restart.content[0].text or "was stopped" in restart.content[0].text


@pytest.mark.anyio
async def test_logs_and_monitoring_and_instance_status() -> None:
    with respx.mock:
        respx.get(f"{BASE}deployment.readLogs").mock(
            return_value=httpx.Response(200, json={"logs": "build ok\n" * 50})
        )
        respx.get(f"{BASE}application.one").mock(
            return_value=httpx.Response(
                200,
                json={"applicationId": "app_1", "appName": "shop-web", "serverId": "srv_1"},
            )
        )
        respx.get(f"{BASE}application.readAppMonitoring").mock(
            return_value=httpx.Response(200, json={"cpu": 0.2, "memory": 128})
        )
        respx.get(f"{BASE}settings.getDokployVersion").mock(return_value=httpx.Response(200, json="v0.26.0"))
        respx.get(f"{BASE}settings.health").mock(return_value=httpx.Response(200, json={"ok": True}))
        respx.get(f"{BASE}settings.getIp").mock(return_value=httpx.Response(403, json={"message": "Forbidden"}))
        respx.get(f"{BASE}settings.getDockerDiskUsage").mock(return_value=httpx.Response(200, json={"images": 3}))
        respx.get(f"{BASE}settings.checkInfrastructureHealth").mock(
            return_value=httpx.Response(200, json={"docker": "ok"})
        )
        server = create_server(client=DokployClient("https://dokploy.example.com", "secret-key"))
        async with Client(server) as client:
            logs = _payload(await client.call_tool("read_deployment_logs", {"deployment_id": "dep_1", "tail": 20}))
            monitoring = _payload(await client.call_tool("get_application_monitoring", {"application_id": "app_1"}))
            status = _payload(await client.call_tool("get_instance_status", {}))

    assert "build ok" in logs["logs"]
    assert logs["tail"] == 20
    assert monitoring["monitoring"]["cpu"] == 0.2
    assert monitoring["appName"] == "shop-web"
    assert status["version"] == "v0.26.0"
    assert status["ip"]["statusCode"] == 403
    assert status["dockerDiskUsage"] == {"images": 3}


@pytest.mark.anyio
async def test_env_update_on_new_application_sends_empty_build_fields() -> None:
    # A freshly created Dokploy application stores buildArgs and buildSecrets as null.
    # saveEnvironment rejects the call when they are missing, so they must be sent as "".
    with respx.mock:
        respx.get(f"{BASE}application.one").mock(
            return_value=httpx.Response(
                200,
                json={
                    "applicationId": "app_new",
                    "env": None,
                    "buildArgs": None,
                    "buildSecrets": None,
                    "createEnvFile": True,
                },
            )
        )
        saved = respx.post(f"{BASE}application.saveEnvironment").mock(
            return_value=httpx.Response(200, json=True)
        )
        server = create_server(client=DokployClient("https://dokploy.example.com", "secret-key"))
        async with Client(server) as client:
            result = await client.call_tool(
                "update_application_env",
                {"application_id": "app_new", "variables": {"PORT": "8787"}},
            )

    assert result.is_error is False
    body = json.loads(saved.calls[0].request.read())
    assert body["env"] == "PORT=8787\n" or body["env"] == "PORT=8787"
    assert body["buildArgs"] == ""
    assert body["buildSecrets"] == ""
    assert body["createEnvFile"] is True


@pytest.mark.anyio
async def test_env_update_keeps_stored_build_fields_when_omitted() -> None:
    with respx.mock:
        respx.get(f"{BASE}application.one").mock(
            return_value=httpx.Response(
                200,
                json={
                    "applicationId": "app_1",
                    "env": "A=1\n",
                    "buildArgs": "NODE_ENV=production",
                    "buildSecrets": "NPM_TOKEN=keep",
                    "createEnvFile": False,
                },
            )
        )
        saved = respx.post(f"{BASE}application.saveEnvironment").mock(
            return_value=httpx.Response(200, json=True)
        )
        server = create_server(client=DokployClient("https://dokploy.example.com", "secret-key"))
        async with Client(server) as client:
            await client.call_tool("update_application_env", {"application_id": "app_1", "env": "B=2\n"})

    body = json.loads(saved.calls[0].request.read())
    assert body["env"] == "B=2\n"
    assert body["buildArgs"] == "NODE_ENV=production"
    assert body["buildSecrets"] == "NPM_TOKEN=keep"
    assert body["createEnvFile"] is False


@pytest.mark.anyio
async def test_env_update_refuses_to_write_back_the_mask() -> None:
    # get_application shows buildSecrets as the mask. Passing it back must not overwrite the real value.
    with respx.mock:
        one = respx.get(f"{BASE}application.one").mock(
            return_value=httpx.Response(200, json={"applicationId": "app_1", "buildSecrets": "NPM_TOKEN=keep"})
        )
        saved = respx.post(f"{BASE}application.saveEnvironment").mock(
            return_value=httpx.Response(200, json=True)
        )
        server = create_server(client=DokployClient("https://dokploy.example.com", "secret-key"))
        async with Client(server) as client:
            result = await client.call_tool(
                "update_application_env",
                {"application_id": "app_1", "build_secrets": "***"},
            )

    assert result.is_error is True
    assert "placeholder" in result.content[0].text
    assert not saved.called
    assert not one.called
