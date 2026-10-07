"""One shared snapshot update for every service sensor."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ServiceAuthError, ServiceClient, ServiceError
from .const import DOMAIN, POLL_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class ServiceCoordinator(DataUpdateCoordinator[dict]):
    """Poll a self-hosted service; never trigger upstream synchronization."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ServiceClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
            always_update=False,
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        """Translate sanitized client failures to Home Assistant retry/reauth."""
        try:
            return await self.client.async_snapshot()
        except ServiceAuthError:
            raise ConfigEntryAuthFailed("Service authentication failed") from None
        except ServiceError:
            raise UpdateFailed("Cannot read service snapshot") from None
