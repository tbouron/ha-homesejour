"""Base entity for HomeSejour."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import HomeSejourCoordinator, PropertyData


class HomeSejourEntity(CoordinatorEntity[HomeSejourCoordinator]):
    """An entity attached to one HomeSejour property (one device per property)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HomeSejourCoordinator, property_id: str, key: str, platform: str
    ) -> None:
        super().__init__(coordinator)
        self.property_id = property_id
        prop = coordinator.data[property_id].property
        self._attr_translation_key = key
        self._attr_unique_id = f"{property_id}_{key}"
        # sensor.homesejour_<property>_<key>, e.g. sensor.homesejour_terroirs_total_earnings
        self.entity_id = f"{platform}.{DOMAIN}_{slugify(prop.reference or prop.name)}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, property_id)},
            name=prop.name,
            manufacturer="HomeSejour",
            model=prop.location,
            serial_number=prop.reference,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://homesejour.fr/activite/plannings",
        )

    @property
    def property_data(self) -> PropertyData | None:
        return self.coordinator.data.get(self.property_id)

    @property
    def available(self) -> bool:
        return super().available and self.property_data is not None
