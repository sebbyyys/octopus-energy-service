"""Installed Home Assistant's coordinator, with real local HTTP responses."""

import importlib
from datetime import timedelta

import pytest

pytest.importorskip("homeassistant")
from aiohttp import web  # noqa: E402
from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady  # noqa: E402
from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

DOMAIN = "octopus_energy_service"
pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_shared_coordinator_poll_interval_and_refresh(hass, aiohttp_server):
    calls = []

    async def snapshot(request):
        calls.append(request.path)
        return web.json_response({"available": False, "stale": True})

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    entry = MockConfigEntry(
        domain=DOMAIN,
        state=ConfigEntryState.SETUP_IN_PROGRESS,
        data={"base_url": str(server.make_url("/")), "token": "test"},
    )
    entry.add_to_hass(hass)
    api = importlib.import_module(f"custom_components.{DOMAIN}.api")
    module = importlib.import_module(f"custom_components.{DOMAIN}.coordinator")
    client = api.ServiceClient(async_get_clientsession(hass), entry.data["base_url"], "test")
    coordinator = module.ServiceCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    assert coordinator.update_interval == timedelta(seconds=300)
    assert coordinator.last_update_success
    assert coordinator.data == {"available": False, "stale": True}
    assert calls == ["/v1/home-assistant"]
    await coordinator.async_shutdown()


@pytest.mark.parametrize(
    "status, error", [(401, ConfigEntryAuthFailed), (500, ConfigEntryNotReady)]
)
async def test_first_refresh_failures_map_to_ha_errors(hass, aiohttp_server, status, error):
    async def snapshot(request):
        return web.Response(status=status)

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    entry = MockConfigEntry(
        domain=DOMAIN,
        state=ConfigEntryState.SETUP_IN_PROGRESS,
        data={"base_url": str(server.make_url("/")), "token": "test"},
    )
    entry.add_to_hass(hass)
    api = importlib.import_module(f"custom_components.{DOMAIN}.api")
    module = importlib.import_module(f"custom_components.{DOMAIN}.coordinator")
    coordinator = module.ServiceCoordinator(
        hass,
        entry,
        api.ServiceClient(async_get_clientsession(hass), entry.data["base_url"], "test"),
    )
    with pytest.raises(error):
        await coordinator.async_config_entry_first_refresh()
    await coordinator.async_shutdown()
