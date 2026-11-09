"""Shared fixtures for HomeSejour tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homesejour.client import Property, parse_plannings
from custom_components.homesejour.const import CONF_TOTP_SECRET, DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
USERNAME = "owner@example.com"
SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every Home Assistant test."""
    return


@pytest.fixture
def properties() -> list[Property]:
    return parse_plannings((FIXTURES / "plannings.html").read_text(encoding="utf-8"))


@pytest.fixture
def mock_client(properties: list[Property]) -> Generator[AsyncMock]:
    """Patch the client used by the integration and the config flow."""
    with (
        patch("custom_components.homesejour.HomeSejourClient", autospec=True) as cls,
        patch("custom_components.homesejour.config_flow.HomeSejourClient", new=cls),
    ):
        client = cls.return_value
        client.async_get_properties.return_value = properties
        client.async_login.return_value = None
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=USERNAME,
        data={"username": USERNAME, "password": "pw", CONF_TOTP_SECRET: SECRET},
    )
