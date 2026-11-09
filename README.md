# dokploy-mcp

An [MCP](https://modelcontextprotocol.io) server for [Dokploy](https://dokploy.com), the self-hosted PaaS. An assistant can list what is on your instance, read deploy and container logs, and deploy, restart, or change env vars. It does not delete anything.

The server uses the official Python MCP SDK. In mcp 2 the high-level class `FastMCP` was renamed to `MCPServer`; this project imports that class as `FastMCP` and uses the same decorator API (`@mcp.tool()`).

Talking to Dokploy is plain HTTP: `x-api-key` against `<DOKPLOY_URL>/api/<router>.<procedure>`, which is how Dokploy publishes its tRPC API. The procedure list was taken from Dokploy's OpenAPI spec (the copy synced to [Dokploy/sdk](https://github.com/Dokploy/sdk)) and from the current router source where that spec leaves response bodies empty.

## Setup

You need two values from the Dokploy panel you want to manage:

1. The panel URL, for example `https://dokploy.example.com`. A path is fine if Dokploy is not at the root. If the URL already ends in `/api`, it is used as-is.
2. An API key. In the panel, open **Settings → Profile**, then the **API/CLI** section, and generate a key. Some versions label this **Settings → API Keys**.

```bash
export DOKPLOY_URL=https://dokploy.example.com
export DOKPLOY_API_KEY=your-api-key
```

Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev
```

## Run locally

stdio is the default. A local client launches the process and talks over stdin and stdout. No bearer token is used, because nothing is listening on the network.

```bash
uv run dokploy-mcp stdio
```

HTTP mode serves the streamable HTTP transport on `/mcp` so a hosted assistant can connect by URL. Set `MCP_AUTH_TOKEN` first. Every request except `GET /health` must send `Authorization: Bearer <MCP_AUTH_TOKEN>`.

```bash
export MCP_AUTH_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uv run dokploy-mcp http --host 127.0.0.1 --port 8765
```

Then the MCP endpoint is `http://127.0.0.1:8765/mcp` and the probe is `http://127.0.0.1:8765/health`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DOKPLOY_URL` | always | Panel origin, with or without `/api` |
| `DOKPLOY_API_KEY` | always | Sent as `x-api-key` |
| `MCP_AUTH_TOKEN` | HTTP mode | Bearer token for `/mcp` |
| `MCP_HOST` | no | Bind address. Default `127.0.0.1`. The image sets `0.0.0.0` |
| `MCP_PORT` | no | Default `8765` |
| `MCP_ALLOWED_HOSTS` | no | Comma-separated `Host` allowlist. Turns DNS-rebinding protection on |
| `MCP_ALLOWED_ORIGINS` | no | Comma-separated `Origin` allowlist, used with `MCP_ALLOWED_HOSTS` |
| `FORWARDED_ALLOW_IPS` | no | Who may set `X-Forwarded-*`. Default `*` |
| `MCP_LOG_LEVEL` | no | Uvicorn log level. Default `info` |

A localhost bind checks the `Host` header and allows only localhost. Binding `0.0.0.0` (the container case, behind Dokploy's proxy) turns that check off, and the bearer token is what keeps the endpoint closed. Set `MCP_ALLOWED_HOSTS` to your public hostname when you want the check back on, for example `dokploy-mcp.example.com`.

## Connect a client

stdio, from a checkout of this repo:

```json
{
  "mcpServers": {
    "dokploy": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/dokploy-mcp", "dokploy-mcp", "stdio"],
      "env": {
        "DOKPLOY_URL": "https://dokploy.example.com",
        "DOKPLOY_API_KEY": "your-api-key"
      }
    }
  }
}
```

Remote HTTP. The client must send the bearer token. Cursor and Claude accept it as a header:

```json
{
  "mcpServers": {
    "dokploy": {
      "url": "https://dokploy-mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer your-mcp-auth-token"
      }
    }
  }
}
```

Use the URL exactly, including `/mcp`. A trailing-slash redirect from an HTTPS proxy back to plain HTTP is refused by the MCP client. The server trusts `X-Forwarded-*` from `FORWARDED_ALLOW_IPS` so that redirect, when it happens, stays on HTTPS.

## Deploy on Dokploy

The image is meant to run as an application on the same Dokploy instance it manages. It calls the panel API; it is not the panel.

1. Create an application from this repo. Build type **Dockerfile**. The repo `Dockerfile` is the build.
2. Set the container port to **8765**.
3. Add environment variables:
   - `DOKPLOY_URL` — the panel's public URL, or `http://dokploy:3000` if the app shares Dokploy's Docker network and that hostname resolves
   - `DOKPLOY_API_KEY`
   - `MCP_AUTH_TOKEN` — a long random string, different from the Dokploy API key
   - `MCP_HOST=0.0.0.0` is already set in the image
4. Add a domain with HTTPS. The proxy must forward `/mcp` and `/health` without stripping the path.
5. Point the health check at path `/health` and port `8765`.
6. Point your MCP client at `https://<that-domain>/mcp` with the bearer token.

`DOKPLOY_URL` has to be the panel, not the MCP server's own domain. The API key can do everything your user can do, and the bearer token lets whoever has it drive those tools. Keep both out of git.

Build the image somewhere else with:

```bash
docker build -t dokploy-mcp .
docker run --rm -p 8765:8765 \
  -e DOKPLOY_URL=https://dokploy.example.com \
  -e DOKPLOY_API_KEY=your-api-key \
  -e MCP_AUTH_TOKEN=your-mcp-auth-token \
  dokploy-mcp
```

## Tools

Reads come first. Status values use Dokploy's vocabulary: `idle` is stopped, `running` means a deployment is in progress, `done` means the last operation succeeded (the service is usually up), and `error` means it failed.

| Tool | What it does |
| --- | --- |
| `list_projects` | Projects and a count of services in each environment |
| `get_project` | One project, with environments, apps, compose services, and databases |
| `list_environments` | Environments in a project, with the services inside them |
| `list_applications` | Applications, optionally filtered by project, environment, or search text |
| `get_application` | Config, status, env file, and containers |
| `list_compose_services` | Compose services, with the same filters |
| `get_compose` | Compose file, env, declared services, and containers |
| `list_databases` | Postgres, MySQL, MariaDB, Mongo, Redis, and LibSQL |
| `get_database` | One database. Password fields are removed |
| `list_deployments` | Deploy history for an app, a compose service, or the whole instance |
| `list_deployment_queue` | Deployments waiting to run |
| `read_deployment_logs` | Build and deploy logs for one deployment |
| `read_application_logs` | Container logs for an application |
| `read_compose_logs` | Logs for one compose container. The id comes from `get_compose` |
| `list_domains` | Domains for an app, a compose service, or the instance |
| `list_servers` | Servers Dokploy can deploy to |
| `get_server` | One server, with secrets redacted |
| `get_server_health` | Docker host health, via `docker.getServerHealth` |
| `get_application_monitoring` | Live app monitoring, via `application.readAppMonitoring` |
| `get_instance_status` | Version, health, IP, Docker disk usage, infrastructure checks |

Writes:

| Tool | What it does |
| --- | --- |
| `deploy_application` / `redeploy_application` | Build and release an application |
| `start_application` / `stop_application` | Start or stop without rebuilding |
| `restart_application` | Reload containers without a new image (`application.reload`) |
| `deploy_compose` / `redeploy_compose` | Deploy a compose service. Volumes are kept |
| `start_compose` / `stop_compose` | Start or stop a compose service |
| `restart_compose` | Stop, then start. Dokploy has no compose restart call. Traffic drops, and if start fails the stack stays stopped |
| `update_application_env` | Replace or merge env vars. Also build args, build secrets, and the env-file flag |
| `update_compose_env` | Replace or merge a compose env file |

Saving env updates the record only. Deploy or restart for running containers to pick it up. Pass `env` to replace the file, or `variables` and `unset` to edit keys. A name in both `variables` and `unset` is removed.

There is no delete tool. `compose.deploy` can take `freshVolumes`, which wipes volumes; that flag is not exposed.

## Development

```bash
uv sync --group dev
uv run pytest
```

Tests mock Dokploy with [respx](https://lundberg.github.io/respx/). One test starts the HTTP server and lists tools through the official MCP client, including a check that a missing bearer token is rejected.

## What the Dokploy API did not make verifiable

I did not have a live Dokploy instance, so nothing here was confirmed against a running panel. The gaps that affected the design:

- The OpenAPI document describes requests and leaves successful responses as empty objects. Nesting (a project contains environments, which contain applications, compose services, and databases) comes from the current router and service source, not from response schemas. Unknown extra fields are passed through when they are small scalars.
- `project.all` for an owner returns database rows with only an id. `list_databases` calls `project.one` or `environment.one`, which include the name and status.
- `application.readAppMonitoring` takes `appName` and has no documented response. The tool returns the JSON unchanged. It does not take a `serverId`, so monitoring for an app on a remote server may not be what you expect.
- `server.getServerMetrics` requires `url`, `token`, and `dataPoints`. The spec does not say how to build those from `server.one`, so the server health tool calls `docker.getServerHealth` instead.
- Settings calls used by `get_instance_status` (`settings.getDokployVersion`, `settings.health`, `settings.getIp`, `settings.getDockerDiskUsage`, `settings.checkInfrastructureHealth`) exist and require the API key. A non-admin key may be forbidden from some of them. Failures are returned next to the checks that worked.
- Operation security in the spec names a scheme called `Authorization`, but the scheme that is actually defined is header `x-api-key`. Requests send `x-api-key`, matching the panel docs.
- There is no compose restart procedure. `restart_compose` is stop followed by start.
- LibSQL has no search procedure. Databases are listed from the project tree so LibSQL is included.
- Deployment and log payloads are not schema'd. Log text is taken from a string body or from a `logs` / `log` / `output` field, then clipped.
