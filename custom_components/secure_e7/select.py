"""Select: boost duration used when the Hot Water switch is turned on."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALLOWED_BOOST_MINUTES, DOMAIN
from .entity import SecureE7Entity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BoostDurationSelect(coordinator)])


class BoostDurationSelect(SecureE7Entity, SelectEntity):
    _attr_icon = "mdi:timer-cog"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [str(m) for m in ALLOWED_BOOST_MINUTES]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "boost_duration", "Boost Duration")

    @property
    def current_option(self) -> str:
        return str(self.coordinator.boost_minutes)

    async def async_select_option(self, option: str) -> None:
        self.coordinator.boost_minutes = int(option)
        self.async_write_ha_state()
