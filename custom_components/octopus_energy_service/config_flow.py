"""Validate local service credentials through Home Assistant's UI."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import ServiceAuthError, ServiceClient, ServiceError, normalize_base_url
from .const import CONF_BASE_URL, CONF_TOKEN, DOMAIN


class ServiceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure the service without using a mutable URL as a unique ID."""

    VERSION = 1

    async def async_step_reauth(self, entry_data):
        """Request a replacement service token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Validate new credentials, update the existing entry and reload."""
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            try:
                await ServiceClient(
                    async_get_clientsession(self.hass),
                    entry.data[CONF_BASE_URL],
                    user_input[CONF_TOKEN],
                ).async_snapshot()
            except ServiceAuthError:
                errors["base"] = "invalid_auth"
            except (ServiceError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_TOKEN: user_input[CONF_TOKEN]},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Change connection details without replacing the entry or entities."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                base_url = normalize_base_url(user_input[CONF_BASE_URL])
                if any(
                    other.entry_id != entry.entry_id and other.data.get(CONF_BASE_URL) == base_url
                    for other in self._async_current_entries()
                ):
                    errors["base"] = "already_configured"
                else:
                    await ServiceClient(
                        async_get_clientsession(self.hass), base_url, user_input[CONF_TOKEN]
                    ).async_snapshot()
            except ValueError:
                errors["base"] = "invalid_url"
            except ServiceAuthError:
                errors["base"] = "invalid_auth"
            except ServiceError:
                errors["base"] = "cannot_connect"
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_BASE_URL: base_url, CONF_TOKEN: user_input[CONF_TOKEN]},
                    reason="reconfigure_successful",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL, default=entry.data[CONF_BASE_URL]): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    ),
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_user(self, user_input=None):
        """Create a config entry after a protected GET succeeds."""
        errors = {}
        if user_input is not None:
            try:
                base_url = normalize_base_url(user_input[CONF_BASE_URL])
                self._async_abort_entries_match({CONF_BASE_URL: base_url})
                await ServiceClient(
                    async_get_clientsession(self.hass), base_url, user_input[CONF_TOKEN]
                ).async_snapshot()
            except ValueError:
                errors["base"] = "invalid_url"
            except ServiceAuthError:
                errors["base"] = "invalid_auth"
            except ServiceError:
                errors["base"] = "cannot_connect"
            else:
                self._async_abort_entries_match({CONF_BASE_URL: base_url})
                return self.async_create_entry(
                    title="Octopus Energy Service",
                    data={CONF_BASE_URL: base_url, CONF_TOKEN: user_input[CONF_TOKEN]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    ),
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )
