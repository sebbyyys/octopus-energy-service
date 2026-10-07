"""Diagnostics must be safe even when arbitrary customer data appears in payloads."""

import importlib
import json

import pytest

pytest.importorskip("homeassistant")

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_diagnostics_redacts_entire_config_and_drops_customer_data(
    hass, service_server, loaded_entry
):
    private = "PRIVATE-CUSTOMER-METER-TOKEN"
    loaded_entry.runtime_data.data.update(
        {
            "account": private,
            "last_sync": private,
            "meters": [{"serial_number": private}],
            "quality": {"complete": True, "warnings": [private]},
        }
    )
    hass.config_entries.async_update_entry(
        loaded_entry,
        data={**loaded_entry.data, "token": private, "unknown_private_setting": private},
        options={"nested": {"token": private}},
    )
    diagnostics = importlib.import_module("custom_components.octopus_energy_service.diagnostics")
    result = await diagnostics.async_get_config_entry_diagnostics(hass, loaded_entry)
    encoded = json.dumps(result)
    assert private not in encoded
    assert service_server["url"] not in encoded
    assert "100.5" not in encoded
    assert result["configuration"] == {"base_url": "**REDACTED**", "token": "**REDACTED**"}
    assert result["snapshot"]["last_sync"] is None
    assert result["snapshot"]["quality_complete"] is True
    assert result["coordinator"]["update_interval_seconds"] == 300
