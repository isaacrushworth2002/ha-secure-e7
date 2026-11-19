"""Binary sensor: live immersion element state."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SecureE7Entity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            HeatingBinarySensor(coordinator),
            DiagnosticBinarySensor(coordinator, "panel_lock", "Panel Lock", "mdi:lock"),
            DiagnosticBinarySensor(
                coordinator, "dst", "Daylight Saving", "mdi:weather-sunny-alert"
            ),
        ]
    )


class HeatingBinarySensor(SecureE7Entity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "heating", "Heating")

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("hot_water_on")


class DiagnosticBinarySensor(SecureE7Entity, BinarySensorEntity):
    """A read-only device setting. Unknown when the reply leaves it out."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator, key, name)
        self._key = key
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get(self._key)
