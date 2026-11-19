"""Switches: hot-water boost, master power, timed boost."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MODE_HOME
from .entity import SecureE7Entity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            HotWaterBoostSwitch(coordinator),
            PowerSwitch(coordinator),
            TimedBoostSwitch(coordinator),
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


class TimedBoostSwitch(SecureE7Entity, SwitchEntity):
    """The app's Timed Boost enable. It gates the boost programme (object 2, empty
    by default) and does not stop the off-peak run that the schedule sets."""

    _attr_icon = "mdi:timer-play-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "timed_boost", "Timed Boost")

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("timed_boost")

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_timed_boost(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_timed_boost(False)
