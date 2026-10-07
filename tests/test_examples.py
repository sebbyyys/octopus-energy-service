"""Offline checks of the documented HA REST contract, not a live HA installation."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "examples/home-assistant/octopus.yaml"


class Secret(str):
    """Retain !secret references without resolving any actual credentials."""


class HALoader(yaml.SafeLoader):
    pass


HALoader.add_constructor("!secret", lambda loader, node: Secret(loader.construct_scalar(node)))
JINJA = Environment(undefined=StrictUndefined)
FIELDS = {
    "electricity": ("total_kwh", "today_kwh", "yesterday_kwh", "month_kwh", "current_rate_pence"),
    "gas": ("total_kwh", "today_kwh", "yesterday_kwh", "month_kwh", "current_rate_pence"),
    "export": ("total_kwh", "today_kwh"),
    "cost": ("today_gbp", "yesterday_gbp", "week_gbp", "month_gbp", "previous_month_gbp"),
    "habits": ("peak_hour", "baseload_kw"),
}


def payload(populated=True):
    result = {
        "available": populated,
        "stale": False,
        "last_sync": "2026-01-02T12:00:00+00:00" if populated else None,
        "latest_reading": "2026-01-02T11:30:00+00:00" if populated else None,
        "currency": "GBP",
        "timezone": "Europe/London",
        "quality": {
            "complete": populated,
            "coverage_percent": 100 if populated else 0,
            "warnings": [],
            "unknown_gas_unit_meters": [],
            "unsupported_tariffs": [],
            "missing_rate_intervals": 0,
            "expected_intervals": 48,
            "observed_intervals": 48 if populated else 0,
        },
    }
    for group, fields in FIELDS.items():
        result[group] = {
            key: (index + 0.25 if populated else None) for index, key in enumerate(fields)
        }
    if populated:
        result["habits"]["peak_hour"] = 18
        result["electricity"]["current_rate_pence"] = -2.5
        result["cost"]["today_gbp"] = -0.5
        result["export"]["today_kwh"] = 0
    return result


def package():
    assert PACKAGE.is_file(), "Home Assistant REST package is missing"
    return yaml.load(PACKAGE.read_text(), Loader=HALoader)


def render(template, data):
    return JINJA.from_string(template).render(value_json=data).strip()


@pytest.mark.parametrize("populated", [False, True])
def test_rest_package_renders_contract_without_turning_null_into_zero(populated):
    rest = package()["rest"]
    assert len(rest) == 1, "All sensors should share one REST request"
    endpoint = rest[0]
    assert endpoint["resource"] == Secret("octopus_service_url")
    assert endpoint["headers"]["Authorization"] == Secret("octopus_service_authorization")
    assert isinstance(endpoint["headers"]["Authorization"], Secret)
    assert endpoint["verify_ssl"] is True
    assert endpoint["scan_interval"] >= 300
    sensors = {sensor["unique_id"]: sensor for sensor in endpoint["sensor"]}
    assert len(sensors) == len(endpoint["sensor"])
    data = payload(populated)
    for group, fields in FIELDS.items():
        for field in fields:
            sensor = sensors[f"octopus_service_{group}_{field}"]
            available = render(sensor["availability"], data)
            assert available == str(populated)
            value = render(sensor["value_template"], data)
            if populated:
                assert float(value) == data[group][field]
            else:
                assert value == "unknown"
    for field in ("last_sync", "latest_reading"):
        sensor = sensors[f"octopus_service_{field}"]
        assert render(sensor["availability"], data) == str(populated)
        assert render(sensor["value_template"], data) == (data[field] or "unknown")
    for sensor in sensors.values():
        assert sensor.get("state_class") != "total_increasing"
        if sensor["unique_id"] in {
            "octopus_service_electricity_total_kwh",
            "octopus_service_gas_total_kwh",
            "octopus_service_export_total_kwh",
        }:
            assert sensor["state_class"] == "total"
            assert sensor["device_class"] == "energy"
            assert sensor["unit_of_measurement"] == "kWh"
        if sensor["unique_id"].startswith("octopus_service_cost_"):
            assert sensor["device_class"] == "monetary"
            assert sensor["unit_of_measurement"] == "GBP"
            assert "state_class" not in sensor
        if sensor["unique_id"].endswith("current_rate_pence"):
            assert sensor["unit_of_measurement"] == "p/kWh"
            assert "device_class" not in sensor


def test_deployment_is_locked_single_worker_private_and_persistent():
    dockerfile_path = ROOT / "Dockerfile"
    assert dockerfile_path.is_file(), "Docker deployment is missing"
    dockerfile = dockerfile_path.read_text()
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "uv.lock" in dockerfile
    assert "COPY pyproject.toml uv.lock README.md LICENSE ./" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert '"--factory"' in dockerfile
    assert "chown 10001:10001 /app/data" in dockerfile
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    service = compose["services"]["octopus"]
    assert service["ports"] == ["${OCTOPUS_BIND_ADDRESS:-127.0.0.1}:8080:8080"]
    assert service["environment"]["OCTOPUS_DB_PATH"] == "/app/data/octopus.sqlite3"
    assert service["volumes"] == ["octopus-data:/app/data"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "octopus-data" in compose["volumes"]


def test_docker_context_excludes_nested_credentials_and_databases():
    patterns = set((ROOT / ".dockerignore").read_text().splitlines())
    assert {
        ".env",
        "**/.env",
        "**/.env.*",
        "**/secrets.yaml",
        "**/secrets.*.yaml",
        "**/*.sqlite*",
        "**/*.db",
        "backups",
    } <= patterns


def test_ci_is_locked_read_only_and_never_publishes():
    workflow_path = ROOT / ".github/workflows/ci.yml"
    assert workflow_path.is_file()
    workflow = yaml.safe_load(workflow_path.read_text())
    assert workflow["permissions"] == {"contents": "read"}
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    scripts = "\n".join(step.get("run", "") for step in steps)
    assert "uv sync --locked" in scripts
    assert "ruff check" in scripts
    assert "pytest" in scripts
    assert "docker build" in scripts
    assert "docker compose" in scripts
    for step in steps:
        assert not any(word in step.get("uses", "") for word in ("upload", "deploy", "login"))
        assert "secrets." not in str(step)
    assert not any(command in scripts for command in ("docker push", "git push", "gh repo"))


@pytest.mark.parametrize("data", [{}, None, payload(False)])
def test_missing_or_empty_response_is_unavailable(data):
    for sensor in package()["rest"][0]["sensor"]:
        if sensor["unique_id"] == "octopus_service_status":
            continue
        assert render(sensor["availability"], data) == "False"
        assert render(sensor["value_template"], data) == "unknown"


def test_stale_and_individually_null_metrics_are_unavailable():
    data = payload()
    sensors = {item["unique_id"]: item for item in package()["rest"][0]["sensor"]}
    data["stale"] = True
    for group, fields in FIELDS.items():
        for field in fields:
            sensor = sensors[f"octopus_service_{group}_{field}"]
            assert render(sensor["availability"], data) == "False"
            assert render(sensor["value_template"], data) == "unknown"
    assert render(sensors["octopus_service_status"]["value_template"], data) == "stale"
    for group, fields in FIELDS.items():
        for field in fields:
            partial = deepcopy(payload())
            partial[group][field] = None
            sensor = sensors[f"octopus_service_{group}_{field}"]
            assert render(sensor["availability"], partial) == "False"
            assert render(sensor["value_template"], partial) == "unknown"
    # A root value_json may be absent after a transport/non-JSON error.
    for sensor in sensors.values():
        assert JINJA.from_string(sensor["availability"]).render().strip() == "False"
        assert JINJA.from_string(sensor["value_template"]).render().strip() == "unknown"


def test_dashboard_references_example_entities_and_secrets_are_placeholders():
    dashboard_path = ROOT / "examples/home-assistant/dashboard.yaml"
    assert dashboard_path.is_file(), "Native dashboard example is missing"
    dashboard = yaml.load(dashboard_path.read_text(), Loader=HALoader)
    assert dashboard["views"]
    entity_ids = {
        "sensor." + sensor["name"].lower().replace(" ", "_")
        for sensor in package()["rest"][0]["sensor"]
    }

    def references(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "entity":
                    yield value
                elif key == "entities":
                    for item in value:
                        if isinstance(item, str):
                            yield item
                        else:
                            yield from references(item)
                else:
                    yield from references(value)
        elif isinstance(node, list):
            for value in node:
                yield from references(value)

    refs = set(references(dashboard))
    assert refs and refs <= entity_ids
    secrets = yaml.safe_load((ROOT / "examples/home-assistant/secrets.example.yaml").read_text())
    assert secrets["octopus_service_url"].endswith("/v1/home-assistant")
    assert secrets["octopus_service_authorization"] == (
        "Bearer REPLACE_WITH_YOUR_LOCALLY_GENERATED_SERVICE_TOKEN"
    )


def test_env_example_is_complete_and_valid_for_offline_mode(monkeypatch):
    from dotenv import dotenv_values

    from octopus_service.settings import Settings

    env_path = ROOT / ".env.example"
    assert env_path.is_file(), "Documented environment template is missing"
    values = dotenv_values(env_path)
    expected = {
        "SERVICE_TOKEN",
        "API_KEY",
        "ACCOUNT_NUMBER",
        "DB_PATH",
        "TIMEZONE",
        "SYNC_INTERVAL_SECONDS",
        "INITIAL_DAYS",
        "LOOKBACK_DAYS",
        "PAYMENT_METHOD",
        "GAS_UNITS_JSON",
        "CALORIFIC_VALUE",
        "CORRECTION_FACTOR",
    }
    assert set(values) == {"OCTOPUS_" + key for key in expected} | {"OCTOPUS_BIND_ADDRESS"}
    for key in values:
        monkeypatch.delenv(key, raising=False)
    assert values["OCTOPUS_SERVICE_TOKEN"] == "", "No shared working token in the example"
    with pytest.raises(ValueError, match="at least 32 characters"):
        Settings(_env_file=env_path)
    settings = Settings(_env_file=env_path, service_token="test-only-token-" + "x" * 32)
    assert settings.api_key.get_secret_value() == ""
    assert settings.account_number == ""
    assert settings.gas_units_json == {}
    assert values["OCTOPUS_BIND_ADDRESS"] == "127.0.0.1"


def test_documentation_entrypoint_has_resolvable_local_links():
    import re

    readme = ROOT / "README.md"
    assert readme.is_file(), "Local setup documentation is missing"
    expected_docs = {
        "api.md",
        "configuration.md",
        "deployment.md",
        "analytics.md",
        "home-assistant.md",
        "security.md",
        "development.md",
    }
    assert expected_docs <= {path.name for path in (ROOT / "docs").glob("*.md")}
    documents = [readme, *(ROOT / "docs").glob("*.md")]
    for document in documents:
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text()):
            if "://" in link or link.startswith("#"):
                continue
            target = document.parent / link.split("#", 1)[0]
            assert target.exists(), f"Broken local link in {document.name}: {link}"
