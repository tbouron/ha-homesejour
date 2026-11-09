"""Constants for the HomeSejour integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from .client import (
    STATE_CLOSED,
    STATE_LOCKED,
    STATE_OPTION,
    STATE_OWNER,
    STATE_RESERVED,
    STATE_TOUR_OPERATOR,
)

DOMAIN: Final = "homesejour"

CONF_TOTP_SECRET: Final = "totp_secret"
CONF_CHECKIN_TIME: Final = "checkin_time"
CONF_CHECKOUT_TIME: Final = "checkout_time"

DEFAULT_CHECKIN_TIME: Final = "17:00:00"
DEFAULT_CHECKOUT_TIME: Final = "10:00:00"

UPDATE_INTERVAL: Final = timedelta(hours=6)

KIND_RENTAL: Final = "rental"
KIND_OWNER: Final = "owner"

# État -> calendar. "closed" is decided by amount, see coordinator.classify_booking.
RENTAL_STATES: Final = frozenset({STATE_RESERVED, STATE_OPTION, STATE_TOUR_OPERATOR})
OWNER_STATES: Final = frozenset({STATE_OWNER, STATE_LOCKED})
CLOSED_STATE: Final = STATE_CLOSED

EVENT_NEW_BOOKING: Final = f"{DOMAIN}_new_booking"

NEW_BOOKING_TEXT: Final = {
    "en": ("🏠 New booking", "From {start} to {end}"),
    "fr": ("🏠 Nouvelle réservation", "Du {start} au {end}"),
}
