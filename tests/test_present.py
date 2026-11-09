from dokploy_mcp.present import database_detail, project_summary, redact


def test_project_summary_counts_services() -> None:
    summary = project_summary(
        {
            "projectId": "proj_1",
            "name": "Shop",
            "description": "Storefront",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "environments": [
                {
                    "environmentId": "env_1",
                    "name": "production",
                    "isDefault": True,
                    "applications": [
                        {"applicationId": "app_1", "name": "web", "applicationStatus": "done"}
                    ],
                    "compose": [{"composeId": "cmp_1", "name": "workers", "composeStatus": "idle"}],
                    "postgres": [{"postgresId": "pg_1"}],
                    "redis": [{"redisId": "rd_1"}],
                }
            ],
        }
    )
    env = summary["environments"][0]
    assert env["applicationCount"] == 1
    assert env["composeCount"] == 1
    assert env["databaseCount"] == 2
    assert "postgresId" not in env


def test_database_detail_redacts_password_and_drops_env() -> None:
    detail = database_detail(
        {
            "postgresId": "pg_1",
            "name": "db",
            "appName": "shop-db",
            "applicationStatus": "done",
            "databaseUser": "shop",
            "databasePassword": "super-secret",
            "databaseName": "shop",
            "env": "DATABASE_PASSWORD=super-secret\n",
        },
        "postgres",
    )
    assert detail["databasePassword"] == "***"
    assert detail["databaseUser"] == "shop"
    assert "env" not in detail
    assert detail["secretsRedacted"] is True


def test_redact_walks_nested_server_tokens() -> None:
    cleaned = redact(
        {
            "metricsConfig": {"server": {"token": "abc", "port": 3000}},
            "sshKey": "-----BEGIN OPENSSH PRIVATE KEY-----",
            "sshKeyId": "key_1",
        }
    )
    assert cleaned["metricsConfig"]["server"]["token"] == "***"
    assert cleaned["metricsConfig"]["server"]["port"] == 3000
    assert cleaned["sshKey"] == "***"
    assert cleaned["sshKeyId"] == "key_1"
