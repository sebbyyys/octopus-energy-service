"""Full native sensor setup through installed Home Assistant's platform loader."""

import pytest

pytest.importorskip("homeassistant")
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

DOMAIN = "octopus_energy_service"
pytestmark = pytest.mark.usefixtures("socket_enabled")
KEYS = (
    "electricity_total_kwh",
    "electricity_today_kwh",
    "electricity_yesterday_kwh",
    "electricity_month_kwh",
    "electricity_current_rate_pence",
    "gas_total_kwh",
    "gas_today_kwh",
    "gas_yesterday_kwh",
    "gas_month_kwh",
    "gas_current_rate_pence",
    "export_total_kwh",
    "export_today_kwh",
    "cost_today_gbp",
    "cost_yesterday_gbp",
    "cost_week_gbp",
    "cost_month_gbp",
    "cost_previous_month_gbp",
    "habits_peak_hour",
    "habits_baseload_kw",
    "last_sync",
    "latest_reading",
    "status",
)


def get_states(hass, entry):
    registry = er.async_get(hass)
    return {
        key: hass.states.get(
            registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{entry.entry_id}_{key}")
        )
        for key in KEYS
    }


async def test_native_entities_contract_and_device(hass, service_server):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Octopus Energy Service",
        data={
            "base_url": service_server["url"],
            "token": "test-token",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    states = get_states(hass, entry)
    assert len(states) == 22  # 21 metric/timestamp fields plus the existing REST status sensor.
    assert all(state is not None for state in states.values())
    assert states["electricity_total_kwh"].state == "100.5"
    assert states["electricity_total_kwh"].attributes["state_class"] == "total"
    assert states["gas_total_kwh"].attributes["state_class"] == "total"
    assert states["export_total_kwh"].attributes["state_class"] == "total"
    assert "state_class" not in states["electricity_today_kwh"].attributes
    assert states["electricity_current_rate_pence"].attributes["unit_of_measurement"] == "p/kWh"
    assert states["cost_today_gbp"].attributes["device_class"] == "monetary"
    assert states["habits_baseload_kw"].attributes["device_class"] == "power"
    assert states["status"].state == "ready"
    assert states["last_sync"].attributes["device_class"] == "timestamp"
    await entry.runtime_data.async_shutdown()
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert device.model == "Local energy data service"
    assert len(service_server["calls"]) == 1
    assert len({state.entity_id for state in states.values()}) == 22


async def test_unload_removes_entities_and_cancels_polling_without_closing_ha_session(
    hass, service_server
):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    entry = MockConfigEntry(
        domain=DOMAIN, data={"base_url": service_server["url"], "token": "test-token"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator._listeners
    session = async_get_clientsession(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(
        state is None or state.state == "unavailable" for state in get_states(hass, entry).values()
    )
    assert not coordinator._listeners
    assert coordinator._unsub_refresh is None
    assert not session.closed


@pytest.mark.parametrize("change", ["stale", "no_data", "missing", "null"])
async def test_numeric_unavailability_does_not_hide_diagnostics(
    hass, service_server, loaded_entry, change
):
    payload = service_server["payload"]
    if change == "stale":
        payload["stale"] = True
    elif change == "no_data":
        payload["available"] = False
    elif change == "missing":
        payload.pop("electricity")
    else:
        payload["electricity"]["total_kwh"] = None
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    states = get_states(hass, loaded_entry)
    assert states["electricity_total_kwh"].state == "unavailable"
    assert states["last_sync"].state != "unavailable"
    assert states["status"].state == (
        "stale" if change == "stale" else "no_data" if change == "no_data" else "ready"
    )


async def test_valid_zero_negative_and_corrections_are_preserved(
    hass, service_server, loaded_entry
):
    states = get_states(hass, loaded_entry)
    assert states["electricity_today_kwh"].state == "0"
    assert states["electricity_current_rate_pence"].state == "-2.5"
    assert states["cost_today_gbp"].state == "-1"
    assert states["habits_peak_hour"].state == "0"
    service_server["payload"]["electricity"]["total_kwh"] = 98.5
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert get_states(hass, loaded_entry)["electricity_total_kwh"].state == "98.5"


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "4.2",
        "private-meter",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,
    ],
)
async def test_malformed_numeric_fields_are_unavailable(hass, service_server, loaded_entry, value):
    service_server["payload"]["electricity"]["total_kwh"] = value
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    states = get_states(hass, loaded_entry)
    assert states["electricity_total_kwh"].state == "unavailable"
    assert states["gas_total_kwh"].state == "50"


@pytest.mark.parametrize("value", [None, [], "meter-private", 42])
async def test_malformed_metric_group_is_safe(hass, service_server, loaded_entry, value):
    service_server["payload"]["electricity"] = value
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert get_states(hass, loaded_entry)["electricity_total_kwh"].state == "unavailable"


@pytest.mark.parametrize(
    "value",
    [None, "", "not-a-date", "2026-10-07T12:00:00", 42, {}, "2026-10-07", "2026-99-07T12:00:00Z"],
)
async def test_invalid_timestamps_are_unavailable_not_crashes(
    hass, service_server, loaded_entry, value
):
    service_server["payload"]["last_sync"] = value
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert get_states(hass, loaded_entry)["last_sync"].state == "unavailable"


@pytest.mark.parametrize("value", [24, -1, 1.5, True, "12"])
async def test_invalid_peak_hour_is_unavailable(hass, service_server, loaded_entry, value):
    service_server["payload"]["habits"]["peak_hour"] = value
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert get_states(hass, loaded_entry)["habits_peak_hour"].state == "unavailable"


async def test_status_quality_attributes_are_allowlisted(hass, service_server, loaded_entry):
    service_server["payload"]["quality"] = {"complete": False, "warnings": ["PRIVATE-METER"]}
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    attributes = get_states(hass, loaded_entry)["status"].attributes
    assert attributes["quality_complete"] is False
    assert attributes["backend_available"] is True
    assert attributes["backend_stale"] is False
    assert "PRIVATE-METER" not in repr(attributes)


async def test_aware_timestamps_normalized_to_utc(hass, service_server, loaded_entry):
    service_server["payload"]["last_sync"] = "2026-10-07T12:30:00+01:00"
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert get_states(hass, loaded_entry)["last_sync"].state == "2026-10-07T11:30:00+00:00"


async def test_failed_poll_hides_all_entities_then_recovers(hass, service_server, loaded_entry):
    service_server["status"] = 500
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert all(state.state == "unavailable" for state in get_states(hass, loaded_entry).values())
    service_server["status"] = 200
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert get_states(hass, loaded_entry)["status"].state == "ready"


async def test_auth_failure_starts_real_reauth_flow(hass, service_server, loaded_entry):
    service_server["status"] = 401
    await loaded_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"
    assert flows[0]["context"]["entry_id"] == loaded_entry.entry_id
    assert loaded_entry.runtime_data._unsub_refresh is None


async def test_periodic_timer_polls_once_for_all_entities(hass, service_server, loaded_entry):
    from datetime import timedelta

    from homeassistant.util.dt import utcnow
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    assert len(service_server["calls"]) == 1
    async_fire_time_changed(hass, utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(service_server["calls"]) == 2
