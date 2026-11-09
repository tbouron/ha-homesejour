"""Tests for setup, sensors, calendars and notifications."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.homesejour.client import (
    Booking,
    CannotConnect,
    InvalidAuth,
    Property,
)
from custom_components.homesejour.const import (
    CONF_CHECKIN_TIME,
    CONF_CHECKOUT_TIME,
    DOMAIN,
    EVENT_NEW_BOOKING,
    UPDATE_INTERVAL,
)
from custom_components.homesejour.coordinator import classify_booking


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("state", "net", "kind"),
    [
        ("owner", 0.0, "owner"),
        ("locked", None, "owner"),
        ("resa", 100.0, "rental"),
        ("option", 100.0, "rental"),
        ("to", 0.0, "rental"),
        ("closed", 850.0, "rental"),
        ("closed", 0.0, "owner"),
        ("cancelled", 600.0, None),
        ("nc", None, None),
        ("something-new", 10.0, None),
    ],
)
def test_classify_booking(state: str, net: float | None, kind: str | None) -> None:
    booking = Booking("1", "CT1", state, date(2027, 1, 1), date(2027, 1, 2), owner_net=net)
    assert classify_booking(booking) == kind


async def test_sensors(hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry) -> None:
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    earnings = hass.states.get("sensor.homesejour_exemple_total_earnings")
    assert float(earnings.state) == 2084.56  # resa 1234.56 + closed 850; owner and cancelled excluded
    assert earnings.attributes["unit_of_measurement"] == "€"
    assert earnings.attributes[2026] == "850.00 €"
    assert earnings.attributes[2027] == "1234.56 €"

    rental = hass.states.get("sensor.homesejour_exemple_rental_days")
    assert rental.state == "12"
    assert rental.attributes["breakdown"] == {2026: {12: 4}, 2027: {1: 1, 4: 7}}

    owner = hass.states.get("sensor.homesejour_exemple_owner_days")
    assert owner.state == "28"
    assert owner.attributes["breakdown"] == {2027: {7: 8, 8: 20}}

    assert hass.states.get("sensor.homesejour_seconde_total_earnings").state == "0"
    assert hass.states.get("sensor.homesejour_seconde_owner_days").state == "0"

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)
    by_id = {next(iter(d.identifiers))[1]: d for d in devices}
    assert set(by_id) == {"74", "81"}
    assert by_id["74"].name == "Résidence EXEMPLE"
    assert by_id["74"].serial_number == "EXEMPLE"
    assert by_id["74"].model == "LA COUARDE SUR MER (17670)"


async def test_calendars(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-09 12:00:00+00:00")
    await _setup(hass, config_entry)

    rental = hass.states.get("calendar.homesejour_exemple_rental")
    assert rental.state == "off"
    assert rental.attributes["message"] == "CT01990"  # next rental: the closed Christmas stay
    owner = hass.states.get("calendar.homesejour_exemple_owner")
    assert owner.attributes["message"] == "CT02058"

    tz = dt_util.get_default_time_zone()
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.homesejour_exemple_rental",
            "start_date_time": datetime(2027, 4, 1, tzinfo=tz),
            "end_date_time": datetime(2027, 5, 31, tzinfo=tz),
        },
        blocking=True,
        return_response=True,
    )
    events = response["calendar.homesejour_exemple_rental"]["events"]
    assert len(events) == 1  # the cancelled May booking is not shown
    assert events[0]["summary"] == "CT02100"
    assert events[0]["start"] == datetime(2027, 4, 12, 17, 0, tzinfo=tz).isoformat()
    assert events[0]["end"] == datetime(2027, 4, 19, 10, 0, tzinfo=tz).isoformat()
    assert events[0]["description"] == "💸 1234.56 €\n\nRéservé via Airbnb"


async def test_calendar_times_from_options(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2027-07-30 12:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="owner@example.com",
        data={"username": "owner@example.com", "password": "pw", "totp_secret": "JBSWY3DPEHPK3PXP"},
        options={CONF_CHECKIN_TIME: "15:30:00", CONF_CHECKOUT_TIME: "09:00:00"},
    )
    await _setup(hass, entry)
    owner = hass.states.get("calendar.homesejour_exemple_owner")
    assert owner.state == "on"  # in the middle of the owner stay
    assert owner.attributes["start_time"] == datetime(2027, 7, 24, 15, 30).strftime("%Y-%m-%d %H:%M:%S")
    assert owner.attributes["end_time"] == datetime(2027, 8, 21, 9, 0).strftime("%Y-%m-%d %H:%M:%S")
    assert owner.attributes["description"] == "Réservé via Agence"


async def test_new_booking_notification(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    properties: list[Property],
    freezer: FrozenDateTimeFactory,
) -> None:
    events = []
    hass.bus.async_listen(EVENT_NEW_BOOKING, events.append)
    await _setup(hass, config_entry)
    assert events == []  # nothing on the first refresh

    new = Booking("2200", "CT02200", "resa", date(2027, 6, 1), date(2027, 6, 8), origin="Airbnb", owner_net=700.0)
    first = properties[0]
    mock_client.async_get_properties.return_value = [
        Property(first.id, first.name, first.reference, first.location, first.bookings + (new,)),
        properties[1],
    ]
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["reference"] == "CT02200"
    assert events[0].data["property_name"] == "Résidence EXEMPLE"
    assert events[0].data["owner_net"] == 700.0
    notifications = hass.data["persistent_notification"]
    assert f"{DOMAIN}_new_booking_2200" in notifications
    assert hass.states.get("sensor.homesejour_exemple_total_earnings").state == "2784.56"


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_properties.side_effect = InvalidAuth
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_setup_retry_when_unreachable(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_properties.side_effect = CannotConnect
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry) -> None:
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
