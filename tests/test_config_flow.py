"""Tests for the HomeSejour config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homesejour.client import CannotConnect, InvalidAuth
from custom_components.homesejour.const import (
    CONF_CHECKIN_TIME,
    CONF_CHECKOUT_TIME,
    CONF_TOTP_SECRET,
    DOMAIN,
)

from .conftest import SECRET, USERNAME


async def _unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Let a pending reload finish, then unload so no refresh timer is left behind."""
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


USER_INPUT = {"username": f" {USERNAME} ", "password": "pw", CONF_TOTP_SECRET: "jbsw y3dp ehpk 3pxp"}


async def test_user_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["data"] == {"username": USERNAME, "password": "pw", CONF_TOTP_SECRET: SECRET}
    assert result["result"].unique_id == USERNAME
    mock_client.async_login.assert_awaited()


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [(InvalidAuth, "invalid_auth"), (CannotConnect, "cannot_connect"), (RuntimeError, "unknown")],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_client: AsyncMock, side_effect: type[Exception], error: str
) -> None:
    mock_client.async_login.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # Recovers once the login works.
    mock_client.async_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_invalid_secret(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_TOTP_SECRET: "not base32 !!"}
    )
    assert result["errors"] == {CONF_TOTP_SECRET: "invalid_totp_secret"}
    mock_client.async_login.assert_not_awaited()


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_client.async_login.side_effect = InvalidAuth
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new", CONF_TOTP_SECRET: SECRET}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.async_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new", CONF_TOTP_SECRET: SECRET}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["password"] == "new"
    await _unload(hass, config_entry)


async def test_reconfigure(hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "other@example.com", "password": "x", CONF_TOTP_SECRET: SECRET}
    )
    assert result["reason"] == "wrong_account"

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": "changed", CONF_TOTP_SECRET: SECRET}
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data["password"] == "changed"
    await _unload(hass, config_entry)


async def test_options_flow(hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CHECKIN_TIME: "16:00:00", CONF_CHECKOUT_TIME: "11:00:00"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_CHECKIN_TIME: "16:00:00", CONF_CHECKOUT_TIME: "11:00:00"}
    await _unload(hass, config_entry)
