"""Calendars for HomeSejour: rental bookings and owner bookings per property."""

from __future__ import annotations

from datetime import datetime, time

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .client import Booking
from .const import (
    CONF_CHECKIN_TIME,
    CONF_CHECKOUT_TIME,
    DEFAULT_CHECKIN_TIME,
    DEFAULT_CHECKOUT_TIME,
    KIND_OWNER,
    KIND_RENTAL,
)
from .coordinator import HomeSejourConfigEntry, HomeSejourCoordinator
from .entity import HomeSejourEntity

PARALLEL_UPDATES = 0

ICONS = {KIND_RENTAL: "mdi:calendar-check-outline", KIND_OWNER: "mdi:calendar-account-outline"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeSejourConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HomeSejourCalendar(coordinator, property_id, kind)
        for property_id in coordinator.data
        for kind in (KIND_RENTAL, KIND_OWNER)
    )


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


class HomeSejourCalendar(HomeSejourEntity, CalendarEntity):
    """Rental or owner bookings of one property."""

    def __init__(self, coordinator: HomeSejourCoordinator, property_id: str, kind: str) -> None:
        super().__init__(coordinator, property_id, kind, Platform.CALENDAR)
        self._kind = kind
        self._attr_icon = ICONS[kind]
        options = coordinator.config_entry.options
        self._checkin = _parse_time(options.get(CONF_CHECKIN_TIME, DEFAULT_CHECKIN_TIME))
        self._checkout = _parse_time(options.get(CONF_CHECKOUT_TIME, DEFAULT_CHECKOUT_TIME))

    def _to_event(self, booking: Booking) -> CalendarEvent:
        tz = dt_util.get_default_time_zone()
        lines: list[str] = []
        if self._kind == KIND_RENTAL and booking.owner_net is not None:
            lines.append(f"💸 {booking.owner_net:.2f} €")
        if booking.origin:
            if lines:
                lines.append("")
            lines.append(f"Réservé via {booking.origin}")
        return CalendarEvent(
            start=datetime.combine(booking.start, self._checkin, tz),
            end=datetime.combine(booking.end, self._checkout, tz),
            summary=booking.reference,
            description="\n".join(lines) or None,
            uid=booking.reference,
        )

    def _events(self) -> list[CalendarEvent]:
        data = self.property_data
        if data is None:
            return []
        return sorted((self._to_event(b) for b in data.bookings(self._kind)), key=lambda e: e.start)

    @property
    def event(self) -> CalendarEvent | None:
        """The current booking, or the next one."""
        now = dt_util.now()
        return next((e for e in self._events() if e.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [e for e in self._events() if e.start < end_date and e.end > start_date]
