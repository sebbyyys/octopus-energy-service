"""Real Home Assistant tests; deliberately separate from backend test dependencies."""

import importlib.util
from datetime import UTC, datetime

import pytest

if importlib.util.find_spec("homeassistant") is not None:
    pytest_plugins = ("pytest_homeassistant_custom_component",)


@pytest.fixture(autouse=True)
def enable_custom_components(request):
    """Allow the real HA loader to load this repository's integration."""
    if importlib.util.find_spec("homeassistant") is not None:
        from pathlib import Path

        import custom_components

        request.getfixturevalue("enable_custom_integrations")
        request.getfixturevalue("monkeypatch").setattr(
            custom_components,
            "__path__",
            [
                str(Path(__file__).resolve().parents[2] / "custom_components"),
                *custom_components.__path__,
            ],
        )


@pytest.fixture
async def service_server(aiohttp_server):
    """An authenticated real HTTP server serving the documented backend contract."""
    from aiohttp import web

    now = datetime.now(UTC).isoformat()
    service = {
        "payload": {
            "available": True,
            "stale": False,
            "last_sync": now,
            "latest_reading": now,
            "currency": "GBP",
            "timezone": "Europe/London",
            "quality": {"complete": True},
            "electricity": {
                "total_kwh": 100.5,
                "today_kwh": 0,
                "yesterday_kwh": 8,
                "month_kwh": 90,
                "current_rate_pence": -2.5,
            },
            "gas": {
                "total_kwh": 50,
                "today_kwh": 3,
                "yesterday_kwh": 4,
                "month_kwh": 40,
                "current_rate_pence": 6,
            },
            "export": {"total_kwh": 25, "today_kwh": 2},
            "cost": {
                "today_gbp": -1,
                "yesterday_gbp": 2,
                "week_gbp": 3,
                "month_gbp": 4,
                "previous_month_gbp": 5,
            },
            "habits": {"peak_hour": 0, "baseload_kw": 0.2},
        },
        "calls": [],
        "status": 200,
    }

    async def snapshot(request):
        service["calls"].append((request.path, request.headers.get("Authorization")))
        if request.headers.get("Authorization") != "Bearer test-token":
            return web.Response(status=401)
        return web.json_response(service["payload"], status=service["status"])

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    service["url"] = str(server.make_url("/")).rstrip("/")
    return service


@pytest.fixture
async def loaded_entry(hass, service_server):
    """Load and reliably unload an actual config entry for entity tests."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="octopus_energy_service",
        title="Octopus Energy Service",
        data={
            "base_url": service_server["url"],
            "token": "test-token",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
