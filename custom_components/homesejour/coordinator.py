"""Data coordinator for HomeSejour."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import Booking, HomeSejourClient, HomeSejourError, InvalidAuth, Property
from .const import (
    CLOSED_STATE,
    DOMAIN,
    EVENT_NEW_BOOKING,
    KIND_OWNER,
    KIND_RENTAL,
    NEW_BOOKING_TEXT,
    OWNER_STATES,
    RENTAL_STATES,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type HomeSejourConfigEntry = ConfigEntry[HomeSejourCoordinator]


def classify_booking(booking: Booking) -> str | None:
    """Return KIND_RENTAL, KIND_OWNER, or None for bookings to ignore."""
    if booking.state in RENTAL_STATES:
        return KIND_RENTAL
    if booking.state in OWNER_STATES:
        return KIND_OWNER
    if booking.state == CLOSED_STATE:
        # A finished stay loses its colour: a zero owner net means it was an owner stay.
        return KIND_RENTAL if (booking.owner_net or 0) > 0 else KIND_OWNER
    return None  # cancelled, unavailable, unknown


@dataclass(frozen=True)
class PropertyData:
    """A property with its bookings split by calendar."""

    property: Property
    rentals: tuple[Booking, ...]
    owners: tuple[Booking, ...]

    def bookings(self, kind: str) -> tuple[Booking, ...]:
        return self.rentals if kind == KIND_RENTAL else self.owners

    @property
    def total_earnings(self) -> float:
        return round(sum(b.owner_net or 0 for b in self.rentals), 2)

    @property
    def earnings_by_year(self) -> dict[int, float]:
        years: dict[int, float] = {}
        for b in self.rentals:
            years[b.start.year] = round(years.get(b.start.year, 0) + (b.owner_net or 0), 2)
        return dict(sorted(years.items()))


def nights_breakdown(bookings: tuple[Booking, ...]) -> dict[int, dict[int, int]]:
    """Count nights per year and month, night by night."""
    breakdown: dict[int, dict[int, int]] = {}
    for b in bookings:
        day: date = b.start
        while day < b.end:
            months = breakdown.setdefault(day.year, {})
            months[day.month] = months.get(day.month, 0) + 1
            day += timedelta(days=1)
    return {y: dict(sorted(m.items())) for y, m in sorted(breakdown.items())}


class HomeSejourCoordinator(DataUpdateCoordinator[dict[str, PropertyData]]):
    """Fetch every property and booking from homesejour.fr."""

    config_entry: HomeSejourConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: HomeSejourConfigEntry, client: HomeSejourClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self._known_rentals: set[str] | None = None

    async def _async_update_data(self) -> dict[str, PropertyData]:
        try:
            properties = await self.client.async_get_properties()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HomeSejourError as err:
            raise UpdateFailed(f"Error communicating with HomeSejour: {err}") from err

        data: dict[str, PropertyData] = {}
        for prop in properties:
            rentals = tuple(b for b in prop.bookings if classify_booking(b) == KIND_RENTAL)
            owners = tuple(b for b in prop.bookings if classify_booking(b) == KIND_OWNER)
            data[prop.id] = PropertyData(prop, rentals, owners)

        self._notify_new_rentals(data)
        return data

    def _notify_new_rentals(self, data: dict[str, PropertyData]) -> None:
        current = {
            f"{pid}:{b.id}": (pdata, b) for pid, pdata in data.items() for b in pdata.rentals
        }
        if self._known_rentals is not None:
            title, message = NEW_BOOKING_TEXT.get(
                self.hass.config.language.split("-")[0], NEW_BOOKING_TEXT["en"]
            )
            for key in current.keys() - self._known_rentals:
                pdata, booking = current[key]
                persistent_notification.async_create(
                    self.hass,
                    message.format(
                        start=booking.start.strftime("%d/%m/%Y"),
                        end=booking.end.strftime("%d/%m/%Y"),
                    ),
                    title=f"{title} — {pdata.property.name}",
                    notification_id=f"{DOMAIN}_new_booking_{booking.id}",
                )
                self.hass.bus.async_fire(
                    EVENT_NEW_BOOKING,
                    {
                        "property_id": pdata.property.id,
                        "property_name": pdata.property.name,
                        "booking_id": booking.id,
                        "reference": booking.reference,
                        "state": booking.state,
                        "start": booking.start.isoformat(),
                        "end": booking.end.isoformat(),
                        "nights": booking.nights,
                        "owner_net": booking.owner_net,
                        "origin": booking.origin,
                    },
                )
        self._known_rentals = set(current)
