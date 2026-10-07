"""Allowlisted diagnostics: no URLs, credentials, readings or meter identifiers."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import parse_timestamp
from .const import CONF_BASE_URL, CONF_TOKEN, POLL_INTERVAL_SECONDS


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Expose health only, never copy config, options or arbitrary snapshot objects."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    quality = data.get("quality")
    complete = quality.get("complete") if isinstance(quality, dict) else None
    timestamps = {key: parse_timestamp(data.get(key)) for key in ("last_sync", "latest_reading")}
    return {
        "configuration": {CONF_BASE_URL: "**REDACTED**", CONF_TOKEN: "**REDACTED**"},
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": POLL_INTERVAL_SECONDS,
        },
        "snapshot": {
            "available": data.get("available") if type(data.get("available")) is bool else None,
            "stale": data.get("stale") if type(data.get("stale")) is bool else None,
            "quality_complete": complete if type(complete) is bool else None,
            **{key: value.isoformat() if value else None for key, value in timestamps.items()},
        },
    }
