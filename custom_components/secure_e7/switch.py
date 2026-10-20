"""Switches: hot-water boost, master power, follow-schedule."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODE_HOME
from .entity import SecureE7Entity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            HotWaterBoostSwitch(coordinator),
            PowerSwitch(coordinator),
            ScheduleSwitch(coordinator),
        ]
    )


class HotWaterBoostSwitch(SecureE7Entity, SwitchEntity):
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "hot_water", "Hot Water")

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("hot_water_on")

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_boost(self.coordinator.boost_minutes)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_cancel_boost()


class PowerSwitch(SecureE7Entity, SwitchEntity):
    _attr_icon = "mdi:power"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "power", "Power")

    @property
    def is_on(self) -> bool | None:
        mode = (self.coordinator.data or {}).get("mode")
        return None if mode is None else mode == MODE_HOME

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_power(False)


class ScheduleSwitch(SecureE7Entity, SwitchEntity):
    _attr_icon = "mdi:calendar-clock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "follow_schedule", "Follow Schedule")

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("schedule_enabled")

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_schedule_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_schedule_enabled(False)
