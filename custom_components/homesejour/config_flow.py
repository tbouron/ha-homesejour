"""Config flow for HomeSejour."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from .client import CannotConnect, HomeSejourClient, InvalidAuth, normalize_totp_secret
from .const import (
    CONF_CHECKIN_TIME,
    CONF_CHECKOUT_TIME,
    CONF_TOTP_SECRET,
    DEFAULT_CHECKIN_TIME,
    DEFAULT_CHECKOUT_TIME,
    DOMAIN,
)
from .coordinator import HomeSejourConfigEntry

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_TOTP_SECRET): PASSWORD_SELECTOR,
    }
)
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_TOTP_SECRET): PASSWORD_SELECTOR,
    }
)


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Log in with the given credentials. Return the cleaned data and form errors."""
    try:
        secret = normalize_totp_secret(data[CONF_TOTP_SECRET])
    except ValueError:
        return data, {CONF_TOTP_SECRET: "invalid_totp_secret"}
    cleaned = {**data, CONF_USERNAME: data[CONF_USERNAME].strip(), CONF_TOTP_SECRET: secret}

    session = async_create_clientsession(hass)
    try:
        await HomeSejourClient(
            session, cleaned[CONF_USERNAME], cleaned[CONF_PASSWORD], secret
        ).async_login()
    except InvalidAuth:
        return cleaned, {"base": "invalid_auth"}
    except CannotConnect:
        return cleaned, {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error while logging in to HomeSejour")
        return cleaned, {"base": "unknown"}
    finally:
        await session.close()
    return cleaned, {}


class HomeSejourConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in to homesejour.fr."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].strip().lower())
            self._abort_if_unique_id_configured()
            data, errors = await _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=data[CONF_USERNAME], data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_USERNAME: (user_input or {}).get(CONF_USERNAME)}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await _validate(
                self.hass, {CONF_USERNAME: entry.data[CONF_USERNAME], **user_input}
            )
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].strip().lower())
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            data, errors = await _validate(self.hass, user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_USERNAME: entry.data[CONF_USERNAME]}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: HomeSejourConfigEntry) -> OptionsFlow:
        return HomeSejourOptionsFlow()


class HomeSejourOptionsFlow(OptionsFlow):
    """Check-in and check-out times used for calendar events."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CHECKIN_TIME,
                        default=options.get(CONF_CHECKIN_TIME, DEFAULT_CHECKIN_TIME),
                    ): TimeSelector(),
                    vol.Required(
                        CONF_CHECKOUT_TIME,
                        default=options.get(CONF_CHECKOUT_TIME, DEFAULT_CHECKOUT_TIME),
                    ): TimeSelector(),
                }
            ),
        )
