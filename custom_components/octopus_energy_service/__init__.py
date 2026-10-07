"""Octopus Energy Service integration (independent from octopus_energy)."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ServiceClient
from .const import CONF_BASE_URL, CONF_TOKEN
from .coordinator import ServiceCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Initialize one shared coordinator and forward native sensors."""
    client = ServiceClient(
        async_get_clientsession(hass), entry.data[CONF_BASE_URL], entry.data[CONF_TOKEN]
    )
    coordinator = ServiceCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload sensors; HA runs the coordinator shutdown hook only on success."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
