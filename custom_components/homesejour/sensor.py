"""Sensors for HomeSejour: earnings, rental days and owner days per property.

Same keys as the Avantio integration (tbouron/ha-avantio); yearly earnings attributes are floats.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, Platform, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HomeSejourConfigEntry, PropertyData, nights_breakdown
from .entity import HomeSejourEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class HomeSejourSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PropertyData], float | int]
    attrs_fn: Callable[[PropertyData], dict[Any, Any]]


SENSORS: tuple[HomeSejourSensorDescription, ...] = (
    HomeSejourSensorDescription(
        key="total_earnings",
        translation_key="total_earnings",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=lambda d: d.total_earnings,
        # {year: 1234.56}, in the sensor's unit (€)
        attrs_fn=lambda d: dict(d.earnings_by_year),
    ),
    HomeSejourSensorDescription(
        key="rental_days",
        translation_key="rental_days",
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda d: sum(b.nights for b in d.rentals),
        attrs_fn=lambda d: {"breakdown": nights_breakdown(d.rentals)},
    ),
    HomeSejourSensorDescription(
        key="owner_days",
        translation_key="owner_days",
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda d: sum(b.nights for b in d.owners),
        attrs_fn=lambda d: {"breakdown": nights_breakdown(d.owners)},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeSejourConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HomeSejourSensor(coordinator, property_id, description)
        for property_id in coordinator.data
        for description in SENSORS
    )


class HomeSejourSensor(HomeSejourEntity, SensorEntity):
    """A HomeSejour statistic for one property."""

    entity_description: HomeSejourSensorDescription

    def __init__(self, coordinator, property_id: str, description: HomeSejourSensorDescription) -> None:
        super().__init__(coordinator, property_id, description.key, Platform.SENSOR)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        data = self.property_data
        return self.entity_description.value_fn(data) if data else None

    @property
    def extra_state_attributes(self) -> dict[Any, Any] | None:
        data = self.property_data
        return self.entity_description.attrs_fn(data) if data else None
