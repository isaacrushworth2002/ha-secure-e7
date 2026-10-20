"""Binary sensor: live immersion element state."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SecureE7Entity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HeatingBinarySensor(coordinator)])


class HeatingBinarySensor(SecureE7Entity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "heating", "Heating")

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("hot_water_on")
