"""Why this exists: Lock the Phoenix observability contract (standardization pass).
What it does: Static checks that (a) traces reach Phoenix from inside Docker,
(b) traces survive container restarts, (c) traces land in a named Phoenix
project instead of "default".
"""

from __future__ import annotations

from pathlib import Path

import yaml

from core.config import Settings
from core.logging import _merge_resource_attribute

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Resource attribute merge → Phoenix project routing
# ---------------------------------------------------------------------------


def test_merge_into_empty_env() -> None:
    merged = _merge_resource_attribute("", "openinference.project.name", "ai-sales-agent")
    assert merged == "openinference.project.name=ai-sales-agent"


def test_merge_appends_to_existing_pairs() -> None:
    merged = _merge_resource_attribute("team=sales", "openinference.project.name", "p1")
    assert merged == "team=sales,openinference.project.name=p1"


def test_merge_never_overwrites_user_value() -> None:
    existing = "openinference.project.name=custom-project"
    merged = _merge_resource_attribute(existing, "openinference.project.name", "default-name")
    assert merged == existing


def test_settings_has_phoenix_project_name_default() -> None:
    assert Settings.model_fields["PHOENIX_PROJECT_NAME"].default == "ai-sales-agent"


# ---------------------------------------------------------------------------
# docker-compose contract — in-network OTLP endpoint + trace persistence
# ---------------------------------------------------------------------------


def test_api_service_points_otlp_at_phoenix_service() -> None:
    env = _compose()["services"]["api"]["environment"]
    assert env["OTLP_ENDPOINT"] == "${OTLP_ENDPOINT:-http://phoenix:4317}"
    assert "PHOENIX_PROJECT_NAME" in env


def test_phoenix_image_is_version_pinned() -> None:
    image = _compose()["services"]["phoenix"]["image"]
    assert not image.endswith(":latest"), "phoenix image must be version-pinned"


def test_phoenix_traces_persist_across_restarts() -> None:
    phoenix = _compose()["services"]["phoenix"]
    assert phoenix["environment"]["PHOENIX_WORKING_DIR"] == "/mnt/data"
    assert any(v.endswith(":/mnt/data") for v in phoenix["volumes"])
