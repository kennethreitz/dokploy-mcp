"""Turn Dokploy payloads into compact records an assistant can read."""

from __future__ import annotations

import json
from typing import Any

DATABASE_ENGINES: dict[str, str] = {
    "postgres": "postgresId",
    "mysql": "mysqlId",
    "mariadb": "mariadbId",
    "mongo": "mongoId",
    "redis": "redisId",
    "libsql": "libsqlId",
}

_SECRET_PARTS = (
    "password",
    "secret",
    "token",
    "privatekey",
    "apikey",
    "credential",
)

# Dokploy stores these on the service row. `env` is kept on detail views because
# editing variables requires the current file. List views drop it.
_BULK_KEYS = {
    "env",
    "previewenv",
    "buildargs",
    "buildsecrets",
    "composefile",
    "command",
    "dockerfile",
    "traefikconfig",
    "deployments",
    "mounts",
    "ports",
    "redirects",
    "security",
    "registry",
    "github",
    "gitlab",
    "bitbucket",
    "gitea",
}

_LOG_KEYS = ("logs", "log", "output", "data", "message", "stdout")
_MAX_LOG_CHARS = 24_000
_MAX_TEXT_FIELD = 500


def is_secret_key(key: str) -> bool:
    folded = key.replace("_", "").replace("-", "").lower()
    if folded in {"sshkey", "privatekey"}:
        return True
    return any(part in folded for part in _SECRET_PARTS)


def as_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "rows", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def collection_meta(payload: Any, shown: int) -> dict[str, Any]:
    total = payload.get("total") if isinstance(payload, dict) else None
    if not isinstance(total, int):
        total = shown
    return {"total": total, "shown": shown}


def redact(value: Any) -> Any:
    """Replace secret-looking fields. Lists and dicts are walked."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if is_secret_key(key):
                cleaned[key] = None if item is None else "***"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def summarize_service(record: Any, *, id_key: str, status_key: str = "applicationStatus") -> dict[str, Any]:
    if not isinstance(record, dict):
        return {id_key: record}
    summary: dict[str, Any] = {}
    for key in (
        id_key,
        "name",
        "appName",
        "description",
        status_key,
        "applicationStatus",
        "composeStatus",
        "sourceType",
        "buildType",
        "dockerImage",
        "repository",
        "owner",
        "branch",
        "customGitUrl",
        "environmentId",
        "projectId",
        "serverId",
        "createdAt",
        "composeType",
    ):
        if key in record and record[key] not in (None, ""):
            summary[key] = record[key]
    server = record.get("server")
    if isinstance(server, dict):
        server_bits = {key: server.get(key) for key in ("serverId", "name") if server.get(key)}
        if server_bits:
            summary["server"] = server_bits
        if "serverId" not in summary and server.get("serverId"):
            summary["serverId"] = server["serverId"]
    return summary


def database_summaries(environment: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for engine, id_key in DATABASE_ENGINES.items():
        rows = environment.get(engine) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            item = summarize_service(row, id_key=id_key)
            item["engine"] = engine
            item.setdefault("environmentId", environment.get("environmentId"))
            found.append(item)
    return found


def environment_summary(environment: dict[str, Any]) -> dict[str, Any]:
    applications = environment.get("applications") or []
    compose = environment.get("compose") or []
    databases = database_summaries(environment)
    return {
        "environmentId": environment.get("environmentId"),
        "name": environment.get("name"),
        "description": environment.get("description"),
        "isDefault": environment.get("isDefault"),
        "projectId": environment.get("projectId") or _nested_id(environment.get("project"), "projectId"),
        "applications": [
            summarize_service(app, id_key="applicationId")
            for app in applications
            if isinstance(app, dict)
        ]
        if isinstance(applications, list)
        else [],
        "compose": [
            summarize_service(item, id_key="composeId", status_key="composeStatus")
            for item in compose
            if isinstance(item, dict)
        ]
        if isinstance(compose, list)
        else [],
        "databases": databases,
    }


def project_summary(project: dict[str, Any], *, detailed: bool = False) -> dict[str, Any]:
    environments = project.get("environments") or []
    env_rows = [environment_summary(env) for env in environments if isinstance(env, dict)]
    summary: dict[str, Any] = {
        "projectId": project.get("projectId"),
        "name": project.get("name"),
        "description": project.get("description"),
        "createdAt": project.get("createdAt"),
    }
    if detailed:
        summary["environments"] = env_rows
    else:
        summary["environments"] = [
            {
                "environmentId": env["environmentId"],
                "name": env["name"],
                "isDefault": env["isDefault"],
                "applicationCount": len(env["applications"]),
                "composeCount": len(env["compose"]),
                "databaseCount": len(env["databases"]),
            }
            for env in env_rows
        ]
    return {key: value for key, value in summary.items() if value is not None}


def application_detail(record: dict[str, Any]) -> dict[str, Any]:
    detail = _detail_record(record, id_key="applicationId", status_key="applicationStatus")
    if record.get("buildSecrets"):
        detail["buildSecrets"] = "***"
    return detail


def compose_detail(record: dict[str, Any]) -> dict[str, Any]:
    detail = _detail_record(record, id_key="composeId", status_key="composeStatus")
    compose_file = record.get("composeFile")
    if isinstance(compose_file, str):
        detail["composeFile"] = _clip(compose_file, 20_000)
    return detail


def database_detail(record: dict[str, Any], engine: str) -> dict[str, Any]:
    detail = public_record(record)
    # public_record already drops the env file, which often repeats the password.
    detail.pop("env", None)
    detail["engine"] = engine
    detail["secretsRedacted"] = True
    return detail


def deployment_summary(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {"value": record}
    preferred = (
        "deploymentId",
        "status",
        "title",
        "description",
        "createdAt",
        "startedAt",
        "finishedAt",
        "errorMessage",
        "applicationId",
        "composeId",
        "buildType",
        "sourceType",
    )
    summary: dict[str, Any] = {}
    for key in preferred:
        value = record.get(key)
        if _is_small_scalar(value):
            summary[key] = value
    for key, value in record.items():
        if key in summary or is_secret_key(key) or not _is_small_scalar(value):
            continue
        summary[key] = value
    return summary


def public_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {"value": record}
    cleaned = redact(record)
    summary: dict[str, Any] = {}
    for key, value in cleaned.items():
        folded = key.lower()
        if folded in _BULK_KEYS:
            continue
        if isinstance(value, dict):
            nested = {
                nested_key: nested_value
                for nested_key, nested_value in value.items()
                if _is_small_scalar(nested_value)
            }
            if nested:
                summary[key] = nested
            continue
        if isinstance(value, list):
            if value and all(_is_small_scalar(item) for item in value) and len(value) <= 20:
                summary[key] = value
            else:
                summary[f"{key}Count"] = len(value)
            continue
        if _is_small_scalar(value):
            summary[key] = value
    return summary


def present_logs(payload: Any, *, tail: int) -> dict[str, Any]:
    text = _log_text(payload)
    truncated = len(text) > _MAX_LOG_CHARS
    if truncated:
        text = text[-_MAX_LOG_CHARS:]
    return {"logs": text, "truncated": truncated, "tail": tail}


def page(items: list[Any], *, limit: int, offset: int) -> list[Any]:
    return items[offset : offset + limit]


def _detail_record(record: dict[str, Any], *, id_key: str, status_key: str) -> dict[str, Any]:
    detail = summarize_service(record, id_key=id_key, status_key=status_key)
    for key in (
        "env",
        "buildArgs",
        "createEnvFile",
        "memoryLimit",
        "memoryReservation",
        "cpuLimit",
        "cpuReservation",
        "replicas",
        "autoDeploy",
        "triggerType",
        "branch",
        "buildPath",
        "dockerContextPath",
        "dockerBuildStage",
        "publishDirectory",
        "isStaticSpa",
        "command",
        "composeType",
        "sourceType",
        "customGitUrl",
        "customGitBranch",
        "customGitSSHKeyId",
        "gitlabBranch",
        "giteaBranch",
        "bitbucketBranch",
        "previewHttps",
        "rollbackActive",
    ):
        if key in record and record[key] is not None:
            value = record[key]
            if isinstance(value, str) and len(value) > 20_000:
                value = _clip(value, 20_000)
            detail[key] = value
    deployments = record.get("deployments")
    if isinstance(deployments, list):
        detail["recentDeployments"] = [deployment_summary(item) for item in deployments[:5]]
        detail["deploymentCount"] = len(deployments)
    return detail


def _log_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        return "\n".join(item if isinstance(item, str) else json.dumps(item, default=str) for item in payload)
    if isinstance(payload, dict):
        for key in _LOG_KEYS:
            value = payload.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return _log_text(value)
        return json.dumps(payload, indent=2, default=str)
    return str(payload)


def _clip(text: str, limit: int) -> dict[str, Any]:
    if len(text) <= limit:
        return {"text": text, "truncated": False}
    return {"text": text[:limit], "truncated": True, "originalLength": len(text)}


def _is_small_scalar(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= _MAX_TEXT_FIELD
    return False


def _nested_id(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None
