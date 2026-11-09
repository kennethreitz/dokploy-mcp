"""Human-sized operations on top of Dokploy's procedure names.

Tools call these. They return plain dicts and raise DokployError on failure.
"""

from __future__ import annotations

import re
from typing import Any

from dokploy_mcp.client import DokployClient
from dokploy_mcp.envfile import EnvFileError, merge_env
from dokploy_mcp.errors import DokployError
from dokploy_mcp.present import (
    DATABASE_ENGINES,
    application_detail,
    as_list,
    collection_meta,
    compose_detail,
    database_detail,
    database_summaries,
    deployment_summary,
    environment_summary,
    page,
    present_logs,
    project_summary,
    public_record,
    summarize_service,
)

_SINCE = re.compile(r"^(all|\d+[smhd])$")
_SEARCH = re.compile(r"^[a-zA-Z0-9 ._-]{0,500}$")
_CONTAINER_ID = re.compile(r"^[a-zA-Z0-9.\-_]+$")

STATUS_NOTE = (
    "Dokploy status values: idle means stopped, running means a deployment is in progress, "
    "done means the last operation succeeded (the service is usually up), error means it failed."
)


def list_projects(client: DokployClient) -> dict[str, Any]:
    projects = as_list(client.get("project.all"))
    rows = [project_summary(project) for project in projects if isinstance(project, dict)]
    return {"projects": rows, "total": len(rows)}


def get_project(client: DokployClient, project_id: str) -> dict[str, Any]:
    project = _expect_dict(client.get("project.one", params={"projectId": project_id}), "project")
    return project_summary(project, detailed=True)


def list_environments(client: DokployClient, project_id: str) -> dict[str, Any]:
    payload = client.get("environment.byProjectId", params={"projectId": project_id})
    rows = [environment_summary(env) for env in as_list(payload) if isinstance(env, dict)]
    return {"projectId": project_id, "environments": rows, "total": len(rows)}


def list_applications(
    client: DokployClient,
    *,
    project_id: str | None = None,
    environment_id: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    if query:
        payload = client.get(
            "application.search",
            params={
                "q": query,
                "projectId": project_id,
                "environmentId": environment_id,
                "limit": limit,
                "offset": offset,
            },
        )
        apps = [
            summarize_service(item, id_key="applicationId")
            for item in as_list(payload)
            if isinstance(item, dict)
        ]
        meta = collection_meta(payload, len(apps))
        return {"applications": apps, **meta, "statusNote": STATUS_NOTE}

    if environment_id:
        environment = _expect_dict(
            client.get("environment.one", params={"environmentId": environment_id}),
            "environment",
        )
        apps = _tag_services(
            environment.get("applications"),
            id_key="applicationId",
            environment=environment,
            project_id=project_id,
        )
    elif project_id:
        apps = _applications_in_project(client, project_id)
    else:
        apps = []
        for project in as_list(client.get("project.all")):
            if not isinstance(project, dict):
                continue
            apps.extend(_services_from_project_tree(project, "applications", "applicationId"))
    selected = page(apps, limit=limit, offset=offset)
    return {
        "applications": selected,
        "total": len(apps),
        "shown": len(selected),
        "statusNote": STATUS_NOTE,
    }


def get_application(client: DokployClient, application_id: str) -> dict[str, Any]:
    record = _expect_dict(
        client.get("application.one", params={"applicationId": application_id}),
        "application",
    )
    detail = application_detail(record)
    detail["statusNote"] = STATUS_NOTE
    app_name = record.get("appName")
    server_id = record.get("serverId")
    if isinstance(server_id, dict):
        server_id = server_id.get("serverId")
    if isinstance(app_name, str) and app_name:
        detail["containers"] = _containers(client, app_name, server_id, kind="service")
    return detail


def list_compose_services(
    client: DokployClient,
    *,
    project_id: str | None = None,
    environment_id: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    if query:
        payload = client.get(
            "compose.search",
            params={
                "q": query,
                "projectId": project_id,
                "environmentId": environment_id,
                "limit": limit,
                "offset": offset,
            },
        )
        rows = [
            summarize_service(item, id_key="composeId", status_key="composeStatus")
            for item in as_list(payload)
            if isinstance(item, dict)
        ]
        return {"composeServices": rows, **collection_meta(payload, len(rows)), "statusNote": STATUS_NOTE}

    if environment_id:
        environment = _expect_dict(
            client.get("environment.one", params={"environmentId": environment_id}),
            "environment",
        )
        rows = _tag_services(
            environment.get("compose"),
            id_key="composeId",
            environment=environment,
            project_id=project_id,
            status_key="composeStatus",
        )
    elif project_id:
        project = _expect_dict(client.get("project.one", params={"projectId": project_id}), "project")
        rows = _services_from_project_tree(project, "compose", "composeId", status_key="composeStatus")
    else:
        rows = []
        for project in as_list(client.get("project.all")):
            if isinstance(project, dict):
                rows.extend(_services_from_project_tree(project, "compose", "composeId", status_key="composeStatus"))
    selected = page(rows, limit=limit, offset=offset)
    return {
        "composeServices": selected,
        "total": len(rows),
        "shown": len(selected),
        "statusNote": STATUS_NOTE,
    }


def get_compose(client: DokployClient, compose_id: str) -> dict[str, Any]:
    record = _expect_dict(client.get("compose.one", params={"composeId": compose_id}), "compose service")
    detail = compose_detail(record)
    detail["statusNote"] = STATUS_NOTE
    try:
        detail["services"] = client.get(
            "compose.loadServices",
            params={"composeId": compose_id, "type": "cache"},
        )
    except DokployError as exc:
        detail["servicesError"] = str(exc)
    app_name = record.get("appName")
    server_id = record.get("serverId")
    if isinstance(app_name, str) and app_name:
        detail["containers"] = _containers(client, app_name, server_id, kind="stack")
    return detail


def list_databases(
    client: DokployClient,
    *,
    project_id: str | None = None,
    environment_id: str | None = None,
) -> dict[str, Any]:
    """List databases with names.

    ``project.all`` often returns database rows that contain only an id. This
    reads ``project.one`` or ``environment.one``, which include the name and status.
    """
    if environment_id:
        environment = _expect_dict(
            client.get("environment.one", params={"environmentId": environment_id}),
            "environment",
        )
        if project_id:
            actual = environment.get("projectId")
            nested = environment.get("project")
            if actual is None and isinstance(nested, dict):
                actual = nested.get("projectId")
            if actual is not None and actual != project_id:
                raise DokployError(f"Environment {environment_id} is not in project {project_id}.")
        databases = database_summaries(environment)
        return {"databases": databases, "total": len(databases), "statusNote": STATUS_NOTE}

    project_ids: list[tuple[str, str | None]]
    if project_id:
        project_ids = [(project_id, None)]
    else:
        project_ids = []
        for project in as_list(client.get("project.all")):
            if isinstance(project, dict) and isinstance(project.get("projectId"), str):
                project_ids.append((project["projectId"], project.get("name")))

    databases: list[dict[str, Any]] = []
    for pid, pname in project_ids:
        project = _expect_dict(client.get("project.one", params={"projectId": pid}), "project")
        name = project.get("name") or pname
        for environment in project.get("environments") or []:
            if not isinstance(environment, dict):
                continue
            for database in database_summaries(environment):
                database["projectId"] = pid
                database["projectName"] = name
                database["environmentName"] = environment.get("name")
                databases.append(database)
    return {"databases": databases, "total": len(databases), "statusNote": STATUS_NOTE}


def get_database(client: DokployClient, engine: str, database_id: str) -> dict[str, Any]:
    if engine not in DATABASE_ENGINES:
        raise DokployError(
            f"Unknown database engine {engine!r}. Use one of: {', '.join(DATABASE_ENGINES)}."
        )
    id_key = DATABASE_ENGINES[engine]
    record = _expect_dict(client.get(f"{engine}.one", params={id_key: database_id}), f"{engine} database")
    detail = database_detail(record, engine)
    detail["statusNote"] = STATUS_NOTE
    return detail


def list_deployments(
    client: DokployClient,
    *,
    application_id: str | None = None,
    compose_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if application_id and compose_id:
        raise DokployError("Pass application_id or compose_id, not both.")
    if application_id:
        payload = client.get("deployment.all", params={"applicationId": application_id})
        scope = {"applicationId": application_id}
    elif compose_id:
        payload = client.get("deployment.allByCompose", params={"composeId": compose_id})
        scope = {"composeId": compose_id}
    else:
        payload = client.get("deployment.allCentralized")
        scope = {"scope": "instance"}
    rows = [deployment_summary(item) for item in as_list(payload)]
    selected = rows[:limit]
    return {"deployments": selected, "total": len(rows), "shown": len(selected), **scope}


def list_deployment_queue(client: DokployClient) -> dict[str, Any]:
    payload = client.get("deployment.queueList")
    rows = [deployment_summary(item) for item in as_list(payload)]
    if not rows and payload not in (None, [], {}):
        return {"queue": payload}
    return {"queue": rows, "total": len(rows)}


def read_deployment_logs(client: DokployClient, deployment_id: str, *, tail: int = 200) -> dict[str, Any]:
    payload = client.get("deployment.readLogs", params={"deploymentId": deployment_id, "tail": tail})
    return {"deploymentId": deployment_id, **present_logs(payload, tail=tail)}


def read_application_logs(
    client: DokployClient,
    application_id: str,
    *,
    tail: int = 200,
    since: str = "all",
    search: str | None = None,
) -> dict[str, Any]:
    _check_since(since)
    _check_search(search)
    payload = client.get(
        "application.readLogs",
        params={"applicationId": application_id, "tail": tail, "since": since, "search": search},
    )
    return {
        "applicationId": application_id,
        "since": since,
        **present_logs(payload, tail=tail),
        "note": "These are container logs. Build logs are on the deployment: use list_deployments and read_deployment_logs.",
    }


def read_compose_logs(
    client: DokployClient,
    compose_id: str,
    container_id: str,
    *,
    tail: int = 200,
    since: str = "all",
    search: str | None = None,
) -> dict[str, Any]:
    _check_since(since)
    _check_search(search)
    if not _CONTAINER_ID.fullmatch(container_id):
        raise DokployError(
            "container_id may contain only letters, numbers, dots, hyphens, and underscores. "
            "Get it from get_compose, which lists containers."
        )
    payload = client.get(
        "compose.readLogs",
        params={
            "composeId": compose_id,
            "containerId": container_id,
            "tail": tail,
            "since": since,
            "search": search,
        },
    )
    return {"composeId": compose_id, "containerId": container_id, "since": since, **present_logs(payload, tail=tail)}


def list_domains(
    client: DokployClient,
    *,
    application_id: str | None = None,
    compose_id: str | None = None,
) -> dict[str, Any]:
    if application_id and compose_id:
        raise DokployError("Pass application_id or compose_id, not both.")
    if application_id:
        payload = client.get("domain.byApplicationId", params={"applicationId": application_id})
        scope: dict[str, Any] = {"applicationId": application_id}
    elif compose_id:
        payload = client.get("domain.byComposeId", params={"composeId": compose_id})
        scope = {"composeId": compose_id}
    else:
        payload = client.get("overview.domains")
        scope = {"scope": "instance"}
    rows = [public_record(item) for item in as_list(payload)]
    if not rows and isinstance(payload, dict):
        return {"domains": public_record(payload), **scope}
    return {"domains": rows, "total": len(rows), **scope}


def list_servers(client: DokployClient) -> dict[str, Any]:
    rows = [public_record(item) for item in as_list(client.get("server.all"))]
    return {"servers": rows, "total": len(rows)}


def get_server(client: DokployClient, server_id: str) -> dict[str, Any]:
    record = _expect_dict(client.get("server.one", params={"serverId": server_id}), "server")
    detail = public_record(record)
    detail["secretsRedacted"] = True
    return detail


def get_server_health(
    client: DokployClient,
    *,
    server_id: str | None = None,
    since_hours: int | None = None,
) -> dict[str, Any]:
    payload = client.get(
        "docker.getServerHealth",
        params={"serverId": server_id, "sinceHours": since_hours},
    )
    return {"serverId": server_id, "sinceHours": since_hours, "health": payload}


def get_application_monitoring(client: DokployClient, application_id: str) -> dict[str, Any]:
    record = _expect_dict(
        client.get("application.one", params={"applicationId": application_id}),
        "application",
    )
    app_name = record.get("appName")
    if not isinstance(app_name, str) or not app_name:
        raise DokployError(f"Application {application_id} has no appName, so monitoring cannot be queried.")
    monitoring = client.get("application.readAppMonitoring", params={"appName": app_name})
    return {
        "applicationId": application_id,
        "appName": app_name,
        "serverId": record.get("serverId"),
        "monitoring": monitoring,
        "note": (
            "Dokploy's application.readAppMonitoring takes the internal appName only. "
            "The OpenAPI spec does not document the response shape, so it is returned unchanged."
        ),
    }


def get_instance_status(client: DokployClient) -> dict[str, Any]:
    """Panel version, health, IP, disk, and infrastructure checks.

    Each call is independent. A non-admin API key can be forbidden from some
    of the settings procedures; those show up as errors instead of failing the rest.
    """
    checks = {
        "version": ("settings.getDokployVersion", None),
        "health": ("settings.health", None),
        "ip": ("settings.getIp", None),
        "dockerDiskUsage": ("settings.getDockerDiskUsage", None),
        "infrastructure": ("settings.checkInfrastructureHealth", None),
    }
    result: dict[str, Any] = {}
    for name, (procedure, params) in checks.items():
        try:
            result[name] = client.get(procedure, params=params)
        except DokployError as exc:
            result[name] = {"error": str(exc), "statusCode": exc.status_code}
    return result


def deploy_application(
    client: DokployClient,
    application_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    body = {"applicationId": application_id, "title": title, "description": description}
    return {
        "applicationId": application_id,
        "action": "deploy",
        "result": client.post("application.deploy", body=body),
    }


def redeploy_application(
    client: DokployClient,
    application_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    body = {"applicationId": application_id, "title": title, "description": description}
    return {
        "applicationId": application_id,
        "action": "redeploy",
        "result": client.post("application.redeploy", body=body),
    }


def start_application(client: DokployClient, application_id: str) -> dict[str, Any]:
    return {
        "applicationId": application_id,
        "action": "start",
        "result": client.post("application.start", body={"applicationId": application_id}),
    }


def stop_application(client: DokployClient, application_id: str) -> dict[str, Any]:
    return {
        "applicationId": application_id,
        "action": "stop",
        "result": client.post("application.stop", body={"applicationId": application_id}),
    }


def restart_application(client: DokployClient, application_id: str) -> dict[str, Any]:
    record = _expect_dict(
        client.get("application.one", params={"applicationId": application_id}),
        "application",
    )
    app_name = record.get("appName")
    if not isinstance(app_name, str) or not app_name:
        raise DokployError(f"Application {application_id} has no appName, so it cannot be reloaded.")
    result = client.post(
        "application.reload",
        body={"applicationId": application_id, "appName": app_name},
    )
    return {
        "applicationId": application_id,
        "appName": app_name,
        "action": "restart",
        "result": result,
        "note": "Called application.reload, which recreates containers without a new image build.",
    }


def deploy_compose(
    client: DokployClient,
    compose_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "composeId": compose_id,
        "action": "deploy",
        "result": client.post(
            "compose.deploy",
            body={"composeId": compose_id, "title": title, "description": description},
        ),
        "note": "Volume reset is not exposed. Deploy keeps existing volumes.",
    }


def redeploy_compose(
    client: DokployClient,
    compose_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "composeId": compose_id,
        "action": "redeploy",
        "result": client.post(
            "compose.redeploy",
            body={"composeId": compose_id, "title": title, "description": description},
        ),
    }


def start_compose(client: DokployClient, compose_id: str) -> dict[str, Any]:
    return {
        "composeId": compose_id,
        "action": "start",
        "result": client.post("compose.start", body={"composeId": compose_id}),
    }


def stop_compose(client: DokployClient, compose_id: str) -> dict[str, Any]:
    return {
        "composeId": compose_id,
        "action": "stop",
        "result": client.post("compose.stop", body={"composeId": compose_id}),
    }


def restart_compose(client: DokployClient, compose_id: str) -> dict[str, Any]:
    stopped = client.post("compose.stop", body={"composeId": compose_id})
    try:
        started = client.post("compose.start", body={"composeId": compose_id})
    except DokployError as exc:
        raise DokployError(
            f"Compose {compose_id} was stopped, but starting it again failed: {exc} The stack is stopped."
        ) from exc
    return {
        "composeId": compose_id,
        "action": "restart",
        "stop": stopped,
        "start": started,
        "note": (
            "Dokploy has no compose restart procedure. The stack was stopped, then started. "
            "That interrupts traffic and does not rebuild images. Use redeploy_compose to rebuild."
        ),
    }


def update_application_env(
    client: DokployClient,
    application_id: str,
    *,
    env: str | None = None,
    variables: dict[str, str] | None = None,
    unset: list[str] | None = None,
    build_args: str | None = None,
    build_secrets: str | None = None,
    create_env_file: bool | None = None,
) -> dict[str, Any]:
    if all(value is None for value in (env, variables, unset, build_args, build_secrets, create_env_file)):
        raise DokployError("Pass env, variables, unset, build_args, build_secrets, or create_env_file.")
    current = _expect_dict(
        client.get("application.one", params={"applicationId": application_id}),
        "application",
    )
    try:
        merged = merge_env(current.get("env") or "", replacement=env, variables=variables, unset=unset)
    except EnvFileError as exc:
        raise DokployError(str(exc)) from exc
    if create_env_file is not None:
        env_file = create_env_file
    elif "createEnvFile" in current:
        env_file = bool(current.get("createEnvFile"))
    else:
        env_file = True
    body = {
        "applicationId": application_id,
        "env": merged,
        "buildArgs": current.get("buildArgs") if build_args is None else build_args,
        "buildSecrets": current.get("buildSecrets") if build_secrets is None else build_secrets,
        "createEnvFile": env_file,
    }
    result = client.post("application.saveEnvironment", body=body)
    return {
        "applicationId": application_id,
        "action": "update-env",
        "env": merged,
        "createEnvFile": body["createEnvFile"],
        "result": result,
        "note": "Saved on the application record. Deploy or restart for running containers to pick it up.",
    }


def update_compose_env(
    client: DokployClient,
    compose_id: str,
    *,
    env: str | None = None,
    variables: dict[str, str] | None = None,
    unset: list[str] | None = None,
    create_env_file: bool | None = None,
) -> dict[str, Any]:
    if all(value is None for value in (env, variables, unset, create_env_file)):
        raise DokployError("Pass env, variables, unset, or create_env_file.")
    current = _expect_dict(client.get("compose.one", params={"composeId": compose_id}), "compose service")
    try:
        merged = merge_env(current.get("env") or "", replacement=env, variables=variables, unset=unset)
    except EnvFileError as exc:
        raise DokployError(str(exc)) from exc
    body: dict[str, Any] = {"composeId": compose_id, "env": merged}
    if create_env_file is not None:
        body["createEnvFile"] = create_env_file
    elif "createEnvFile" in current:
        body["createEnvFile"] = bool(current.get("createEnvFile"))
    result = client.post("compose.saveEnvironment", body=body)
    return {
        "composeId": compose_id,
        "action": "update-env",
        "env": merged,
        "result": result,
        "note": "Saved on the compose record. Deploy or restart for running containers to pick it up.",
    }


def _applications_in_project(client: DokployClient, project_id: str) -> list[dict[str, Any]]:
    project = _expect_dict(client.get("project.one", params={"projectId": project_id}), "project")
    return _services_from_project_tree(project, "applications", "applicationId")


def _services_from_project_tree(
    project: dict[str, Any],
    collection: str,
    id_key: str,
    *,
    status_key: str = "applicationStatus",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for environment in project.get("environments") or []:
        if not isinstance(environment, dict):
            continue
        rows.extend(
            _tag_services(
                environment.get(collection),
                id_key=id_key,
                environment=environment,
                project_id=project.get("projectId"),
                project_name=project.get("name"),
                status_key=status_key,
            )
        )
    return rows


def _tag_services(
    services: Any,
    *,
    id_key: str,
    environment: dict[str, Any],
    project_id: str | None,
    project_name: str | None = None,
    status_key: str = "applicationStatus",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(services, list):
        return rows
    for service in services:
        if not isinstance(service, dict):
            continue
        item = summarize_service(service, id_key=id_key, status_key=status_key)
        item["environmentId"] = environment.get("environmentId")
        item["environmentName"] = environment.get("name")
        if project_id:
            item["projectId"] = project_id
        if project_name:
            item["projectName"] = project_name
        rows.append(item)
    return rows


def _containers(
    client: DokployClient,
    app_name: str,
    server_id: Any,
    *,
    kind: str,
) -> dict[str, Any]:
    procedure = "docker.getServiceContainersByAppName" if kind == "service" else "docker.getStackContainersByAppName"
    server = server_id if isinstance(server_id, str) else None
    try:
        payload = client.get(procedure, params={"appName": app_name, "serverId": server})
    except DokployError as exc:
        return {"error": str(exc), "statusCode": exc.status_code}
    containers = [public_record(item) for item in as_list(payload)]
    if not containers and payload not in (None, [], {}):
        return {"raw": payload}
    return {"containers": containers, "total": len(containers)}


def _expect_dict(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DokployError(f"Dokploy returned an unexpected {label} payload.")
    return payload


def _check_since(since: str) -> None:
    if not _SINCE.fullmatch(since):
        raise DokployError("since must be 'all' or a duration like 10s, 5m, 2h, or 1d.")


def _check_search(search: str | None) -> None:
    if search is None:
        return
    if not _SEARCH.fullmatch(search):
        raise DokployError(
            "search may only contain letters, numbers, spaces, dots, underscores, and hyphens (max 500)."
        )
