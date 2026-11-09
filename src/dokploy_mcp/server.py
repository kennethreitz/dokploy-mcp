"""Dokploy MCP server.

The official Python SDK renamed ``FastMCP`` to ``MCPServer`` in mcp 2. The
decorator API is the same one, and this module imports it under the FastMCP name.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from mcp.server import MCPServer as FastMCP
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from dokploy_mcp import actions
from dokploy_mcp.client import DokployClient
from dokploy_mcp.errors import DokployError

INSTRUCTIONS = """\
You manage one Dokploy instance, a self-hosted PaaS, through its API.

Read before you change anything. applicationStatus and composeStatus use Dokploy's vocabulary: idle means stopped, running means a deployment is in progress, done means the last operation succeeded (the service is usually up), error means it failed.

Saving environment variables updates the stored config only. Deploy or restart for running containers to pick the new values up.

This server cannot delete projects, applications, compose stacks, databases, domains, or deployments. Do not invent a delete step.

Database passwords and server secrets are omitted. Application and compose env files are included because editing variables requires the current file. Treat them as secrets.

restart_compose stops the stack and then starts it. There is a short outage, and it does not rebuild images. Use redeploy_compose to rebuild.
"""

READ = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
CHANGE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
DISRUPT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)

ApplicationId = Annotated[str, Field(min_length=1, description="applicationId from list_applications or get_project.")]
ComposeId = Annotated[str, Field(min_length=1, description="composeId from list_compose_services or get_project.")]
ProjectId = Annotated[str, Field(min_length=1, description="projectId from list_projects.")]
EnvironmentId = Annotated[str, Field(min_length=1, description="environmentId from list_environments or get_project.")]
DatabaseEngine = Literal["postgres", "mysql", "mariadb", "mongo", "redis", "libsql"]
Limit = Annotated[int, Field(ge=1, le=100)]
Tail = Annotated[int, Field(ge=1, le=10000)]
SinceHours = Annotated[int, Field(ge=1, le=168)]


def create_server(*, client: DokployClient | None = None) -> FastMCP:
    """Build a server. Pass a client in tests; otherwise tools read DOKPLOY_* at call time."""
    holder: dict[str, DokployClient | None] = {"client": client}

    def get_client() -> DokployClient:
        if holder["client"] is None:
            holder["client"] = DokployClient.from_env()
        assert holder["client"] is not None
        return holder["client"]

    def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_client(), *args, **kwargs)
        except DokployError as exc:
            raise ToolError(str(exc)) from exc

    mcp = FastMCP(
        "Dokploy",
        instructions=INSTRUCTIONS,
        website_url="https://dokploy.com",
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @mcp.tool(annotations=READ)
    def list_projects() -> dict[str, Any]:
        """List projects on this Dokploy instance, with a count of services in each environment."""
        return call(actions.list_projects)

    @mcp.tool(annotations=READ)
    def get_project(project_id: ProjectId) -> dict[str, Any]:
        """Get one project, including its environments, applications, compose services, and databases."""
        return call(actions.get_project, project_id)

    @mcp.tool(annotations=READ)
    def list_environments(project_id: ProjectId) -> dict[str, Any]:
        """List environments in a project, with the services inside each one."""
        return call(actions.list_environments, project_id)

    @mcp.tool(annotations=READ)
    def list_applications(
        project_id: str | None = None,
        environment_id: str | None = None,
        query: str | None = None,
        limit: Limit = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """List applications. Filter by project, environment, or a search query.

        Status uses Dokploy's vocabulary: idle is stopped, running is a deploy in
        progress, done means the last operation succeeded, error means it failed.
        """
        return call(
            actions.list_applications,
            project_id=project_id,
            environment_id=environment_id,
            query=query,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(annotations=READ)
    def get_application(application_id: ApplicationId) -> dict[str, Any]:
        """Get an application's config, status, env file, and current containers.

        The env file is included and may contain secrets. buildSecrets are redacted.
        Container lookup is best-effort and is omitted as an error field when Dokploy refuses it.
        """
        return call(actions.get_application, application_id)

    @mcp.tool(annotations=READ)
    def list_compose_services(
        project_id: str | None = None,
        environment_id: str | None = None,
        query: str | None = None,
        limit: Limit = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """List Docker Compose services. Filter by project, environment, or a search query."""
        return call(
            actions.list_compose_services,
            project_id=project_id,
            environment_id=environment_id,
            query=query,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(annotations=READ)
    def get_compose(compose_id: ComposeId) -> dict[str, Any]:
        """Get a compose service: file, env, declared services, status, and containers.

        Use a container id from this result with read_compose_logs.
        """
        return call(actions.get_compose, compose_id)

    @mcp.tool(annotations=READ)
    def list_databases(
        project_id: str | None = None,
        environment_id: str | None = None,
    ) -> dict[str, Any]:
        """List Postgres, MySQL, MariaDB, MongoDB, Redis, and LibSQL databases.

        Passwords are not included. Use get_database for connection details other than the password.
        """
        return call(actions.list_databases, project_id=project_id, environment_id=environment_id)

    @mcp.tool(annotations=READ)
    def get_database(engine: DatabaseEngine, database_id: str) -> dict[str, Any]:
        """Get one database. Password fields are redacted.

        engine is postgres, mysql, mariadb, mongo, redis, or libsql.
        database_id is that engine's id, for example postgresId.
        """
        return call(actions.get_database, engine, database_id)

    @mcp.tool(annotations=READ)
    def list_deployments(
        application_id: str | None = None,
        compose_id: str | None = None,
        limit: Limit = 20,
    ) -> dict[str, Any]:
        """List deployments for an application, a compose service, or the whole instance.

        Pass one id. Omit both to read the centralized deployment list. The newest
        rows Dokploy returns are kept, up to limit. Build output is not included;
        call read_deployment_logs with a deploymentId.
        """
        return call(
            actions.list_deployments,
            application_id=application_id,
            compose_id=compose_id,
            limit=limit,
        )

    @mcp.tool(annotations=READ)
    def list_deployment_queue() -> dict[str, Any]:
        """List deployments waiting in Dokploy's queue."""
        return call(actions.list_deployment_queue)

    @mcp.tool(annotations=READ)
    def read_deployment_logs(deployment_id: str, tail: Tail = 200) -> dict[str, Any]:
        """Read build and deploy logs for one deployment. tail is how many lines to request (1-10000)."""
        return call(actions.read_deployment_logs, deployment_id, tail=tail)

    @mcp.tool(annotations=READ)
    def read_application_logs(
        application_id: ApplicationId,
        tail: Tail = 200,
        since: str = "all",
        search: str | None = None,
    ) -> dict[str, Any]:
        """Read an application's container logs, not its build logs.

        since is 'all' or a duration like 10s, 5m, 2h, or 1d. search is an optional
        plain string of letters, numbers, spaces, dots, underscores, and hyphens.
        """
        return call(
            actions.read_application_logs,
            application_id,
            tail=tail,
            since=since,
            search=search,
        )

    @mcp.tool(annotations=READ)
    def read_compose_logs(
        compose_id: ComposeId,
        container_id: str,
        tail: Tail = 200,
        since: str = "all",
        search: str | None = None,
    ) -> dict[str, Any]:
        """Read logs for one container in a compose service.

        container_id comes from get_compose. since is 'all' or a duration like 5m or 2h.
        """
        return call(
            actions.read_compose_logs,
            compose_id,
            container_id,
            tail=tail,
            since=since,
            search=search,
        )

    @mcp.tool(annotations=READ)
    def list_domains(
        application_id: str | None = None,
        compose_id: str | None = None,
    ) -> dict[str, Any]:
        """List domains for an application, a compose service, or the whole instance."""
        return call(actions.list_domains, application_id=application_id, compose_id=compose_id)

    @mcp.tool(annotations=READ)
    def list_servers() -> dict[str, Any]:
        """List servers Dokploy can deploy to. Secrets in the payload are redacted."""
        return call(actions.list_servers)

    @mcp.tool(annotations=READ)
    def get_server(server_id: str) -> dict[str, Any]:
        """Get one remote server. SSH material and tokens are redacted."""
        return call(actions.get_server, server_id)

    @mcp.tool(annotations=READ)
    def get_server_health(server_id: str | None = None, since_hours: SinceHours | None = None) -> dict[str, Any]:
        """Get Docker host health. Omit server_id for the Dokploy host itself.

        since_hours is 1-168 when you want a window. This uses docker.getServerHealth.
        server.getServerMetrics is not wrapped: it requires a monitoring URL and token
        the OpenAPI spec does not explain how to derive.
        """
        return call(actions.get_server_health, server_id=server_id, since_hours=since_hours)

    @mcp.tool(annotations=READ)
    def get_application_monitoring(application_id: ApplicationId) -> dict[str, Any]:
        """Read live monitoring for an application (CPU, memory, and network when Dokploy provides them).

        The monitoring response shape is not in the OpenAPI spec, so it is returned as Dokploy sent it.
        """
        return call(actions.get_application_monitoring, application_id)

    @mcp.tool(annotations=READ)
    def get_instance_status() -> dict[str, Any]:
        """Read Dokploy version, health, public IP, Docker disk usage, and infrastructure checks.

        Each check is independent. A non-admin key may be forbidden from some settings calls;
        those are returned as errors alongside the checks that succeeded.
        """
        return call(actions.get_instance_status)

    @mcp.tool(annotations=CHANGE)
    def deploy_application(
        application_id: ApplicationId,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Start a deployment for an application. This builds and releases the current source."""
        return call(actions.deploy_application, application_id, title=title, description=description)

    @mcp.tool(annotations=CHANGE)
    def redeploy_application(
        application_id: ApplicationId,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Redeploy an application from its current configuration."""
        return call(actions.redeploy_application, application_id, title=title, description=description)

    @mcp.tool(annotations=CHANGE)
    def start_application(application_id: ApplicationId) -> dict[str, Any]:
        """Start a stopped application without rebuilding it."""
        return call(actions.start_application, application_id)

    @mcp.tool(annotations=DISRUPT)
    def stop_application(application_id: ApplicationId) -> dict[str, Any]:
        """Stop an application. Running containers go down. Nothing is deleted."""
        return call(actions.stop_application, application_id)

    @mcp.tool(annotations=DISRUPT)
    def restart_application(application_id: ApplicationId) -> dict[str, Any]:
        """Restart an application by reloading its containers, without building a new image.

        This calls Dokploy's application.reload. Use redeploy_application to rebuild.
        """
        return call(actions.restart_application, application_id)

    @mcp.tool(annotations=CHANGE)
    def deploy_compose(
        compose_id: ComposeId,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Deploy a compose service. Existing volumes are kept.

        Dokploy can wipe volumes with freshVolumes. This server does not expose that flag.
        """
        return call(actions.deploy_compose, compose_id, title=title, description=description)

    @mcp.tool(annotations=CHANGE)
    def redeploy_compose(
        compose_id: ComposeId,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Redeploy a compose service from its current file and source. Volumes are kept."""
        return call(actions.redeploy_compose, compose_id, title=title, description=description)

    @mcp.tool(annotations=CHANGE)
    def start_compose(compose_id: ComposeId) -> dict[str, Any]:
        """Start a stopped compose service."""
        return call(actions.start_compose, compose_id)

    @mcp.tool(annotations=DISRUPT)
    def stop_compose(compose_id: ComposeId) -> dict[str, Any]:
        """Stop a compose service. Containers go down. Volumes and the compose record stay."""
        return call(actions.stop_compose, compose_id)

    @mcp.tool(annotations=DISRUPT)
    def restart_compose(compose_id: ComposeId) -> dict[str, Any]:
        """Restart a compose service by stopping it and starting it again.

        Dokploy has no single compose restart procedure. Traffic drops between the two calls,
        and images are not rebuilt. If start fails, the stack is left stopped.
        """
        return call(actions.restart_compose, compose_id)

    @mcp.tool(annotations=DISRUPT)
    def update_application_env(
        application_id: ApplicationId,
        env: str | None = None,
        variables: dict[str, str] | None = None,
        unset: list[str] | None = None,
        build_args: str | None = None,
        build_secrets: str | None = None,
        create_env_file: bool | None = None,
    ) -> dict[str, Any]:
        """Update an application's environment variables.

        Pass env to replace the whole file, or variables to set keys and unset to remove
        keys. Those edits merge into the current file unless you also pass env. build_args
        and build_secrets are left unchanged unless you pass them. create_env_file controls
        whether Dokploy writes an env file into the build. This does not redeploy.
        To change one secret, pass only variables={KEY: value}; other keys are kept.
        Never pass the *** mask from get_application back as a value.
        """
        return call(
            actions.update_application_env,
            application_id,
            env=env,
            variables=variables,
            unset=unset,
            build_args=build_args,
            build_secrets=build_secrets,
            create_env_file=create_env_file,
        )

    @mcp.tool(annotations=DISRUPT)
    def update_compose_env(
        compose_id: ComposeId,
        env: str | None = None,
        variables: dict[str, str] | None = None,
        unset: list[str] | None = None,
        create_env_file: bool | None = None,
    ) -> dict[str, Any]:
        """Update a compose service's environment variables.

        Pass env to replace the file, or variables and unset to merge into the current file.
        This does not redeploy. When a name is in both variables and unset, it is removed.
        """
        return call(
            actions.update_compose_env,
            compose_id,
            env=env,
            variables=variables,
            unset=unset,
            create_env_file=create_env_file,
        )

    return mcp
