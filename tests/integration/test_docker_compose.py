"""Integration checks for Docker Compose deployment (US4)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from urllib.request import urlopen

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DOCKER_INTEGRATION_TESTS") != "1",
    reason="Set RUN_DOCKER_INTEGRATION_TESTS=1 to run Docker Compose integration tests",
)


@pytest.fixture(scope="module")
def compose_up():
    env = os.environ.copy()
    env["DB_USER"] = "user"
    env["DB_PASSWORD"] = "password"
    env["DB_NAME"] = "ai_agent"
    env["DB_PORT"] = "5432"
    env["API_PORT"] = "18000"
    env["DATABASE_POOL_SIZE"] = "5"
    env["DATABASE_MAX_OVERFLOW"] = "0"
    # Compose now refuses to start without an explicit webhook secret.
    env.setdefault("TELEGRAM_WEBHOOK_SECRET", "compose_test_secret_1234567890")
    subprocess.run(
        ["docker", "compose", "up", "-d", "--build"],
        check=True,
        env=env,
    )
    try:
        yield
    finally:
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            check=False,
        )


def _wait_until_healthy(service_name: str, timeout_seconds: int = 60) -> None:
    start = time.time()
    while time.time() - start < timeout_seconds:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", service_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            return
        time.sleep(2)
    msg = f"Service {service_name} did not become healthy within {timeout_seconds}s"
    raise AssertionError(msg)


def test_docker_compose_starts_services_successfully(compose_up: None) -> None:
    result = subprocess.run(
        ["docker", "compose", "ps", "--services", "--filter", "status=running"],
        capture_output=True,
        text=True,
        check=True,
    )
    running = set(result.stdout.split())
    assert {"api", "db", "phoenix"}.issubset(running)


def test_health_checks_pass_within_60_seconds(compose_up: None) -> None:
    _wait_until_healthy("ai-agent-api", timeout_seconds=60)
    _wait_until_healthy("ai-agent-postgres", timeout_seconds=60)


def test_api_responds_after_startup(compose_up: None) -> None:
    api_port = os.getenv("API_PORT", "18000")
    with urlopen(f"http://localhost:{api_port}/health/readiness", timeout=10) as response:
        status_code = response.status
        payload = json.loads(response.read().decode("utf-8"))
    assert status_code in {200, 503}
    assert "status" in payload
    assert "checks" in payload


def test_container_restart_on_failure(compose_up: None) -> None:
    before = subprocess.run(
        ["docker", "inspect", "--format", "{{.RestartCount}}", "ai-agent-api"],
        capture_output=True,
        text=True,
        check=True,
    )
    before_count = int(before.stdout.strip())

    subprocess.run(
        ["docker", "exec", "ai-agent-api", "sh", "-lc", "kill 1"],
        check=True,
    )
    time.sleep(10)
    _wait_until_healthy("ai-agent-api", timeout_seconds=60)

    after = subprocess.run(
        ["docker", "inspect", "--format", "{{.RestartCount}}", "ai-agent-api"],
        capture_output=True,
        text=True,
        check=True,
    )
    after_count = int(after.stdout.strip())
    assert after_count >= before_count + 1


def test_db_connection_pool_under_20_connections(compose_up: None) -> None:
    api_port = os.getenv("API_PORT", "18000")
    for _ in range(10):
        with urlopen(f"http://localhost:{api_port}/health/readiness", timeout=10) as response:
            assert response.status == 200

    db_name = "ai_agent"
    db_port = os.getenv("DB_PORT", "5432")
    engine = create_engine(f"postgresql+psycopg://user:password@localhost:{db_port}/{db_name}")
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT count(*) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :dbname AND usename = :dbuser "
                    "AND state = 'active' "
                    "AND pid <> pg_backend_pid()"
                ),
                {"dbname": db_name, "dbuser": "user"},
            )
            active_connections = int(result.scalar_one())
            assert active_connections <= 20
    finally:
        engine.dispose()
