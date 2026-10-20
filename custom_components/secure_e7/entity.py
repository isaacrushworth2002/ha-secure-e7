"""Shared base entity for Secure E7+ (grouped under one device)."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SecureE7Coordinator


class SecureE7Entity(CoordinatorEntity[SecureE7Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SecureE7Coordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator._mac}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator._mac)},
            name="Immersion Heater",
            manufacturer="Secure / Horstmann",
            model="E7+ (SCE100)",
        )
