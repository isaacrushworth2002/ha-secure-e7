"""Sensors: mode, boost remaining, next scheduled change, estimated live power,
and REAL metered daily energy (from the device's DynamicTariff consumption path).

Per-day metered figures are boost/off-peak amp-hours the device accumulates,
converted to kWh with a nominal 230 V (exactly as the vendor app does). Estimated
live power (rating x on/off) is kept only as a between-days live proxy, since the
device reports energy daily, not instantaneously.
"""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MODE_NAMES
from .schedule import DAYS
from .entity import SecureE7Entity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ModeSensor(coordinator),
            BoostRemainingSensor(coordinator),
            NextChangeTimeSensor(coordinator),
            NextChangeStateSensor(coordinator),
            EstimatedPowerSensor(coordinator),
            EnergyTodaySensor(coordinator, "energy_today_kwh", "Energy Today", total=True),
            EnergyTodaySensor(coordinator, "energy_today_boost_kwh", "Energy Today Boost"),
            EnergyTodaySensor(coordinator, "energy_today_offpeak_kwh", "Energy Today Off-Peak"),
            HoursTodaySensor(coordinator, "hours_today_boost", "Boost Hours Today"),
            HoursTodaySensor(coordinator, "hours_today_offpeak", "Off-Peak Hours Today"),
            ScheduleSensor(coordinator),
        ]
    )


class ModeSensor(SecureE7Entity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MODE_NAMES.values())
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mode", "Mode")

    @property
    def native_value(self) -> str | None:
        return MODE_NAMES.get((self.coordinator.data or {}).get("mode"))


class BoostRemainingSensor(SecureE7Entity, SensorEntity):
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "boost_remaining", "Boost Remaining")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("boost_minutes_remaining") or 0


class NextChangeTimeSensor(SecureE7Entity, SensorEntity):
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "next_change_time", "Next Change Time")

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data or {}).get("next_change_time")


class NextChangeStateSensor(SecureE7Entity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["on", "off"]
    _attr_icon = "mdi:cards-playing-outline"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "next_change_state", "Next Change State")

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data or {}).get("next_change_state")


class EstimatedPowerSensor(SecureE7Entity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "estimated_power", "Estimated Power")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("estimated_power")


class EnergyTodaySensor(SecureE7Entity, SensorEntity):
    """Latest day's metered kWh. TOTAL with a daily last_reset."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator, key: str, name: str, total: bool = False) -> None:
        super().__init__(coordinator, key, name)
        self._key = key
        self._is_total = total

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._key)

    @property
    def last_reset(self):
        days = (self.coordinator.data or {}).get("consumption") or []
        if not days:
            return None
        return datetime.fromtimestamp(days[-1]["ts"], tz=timezone.utc)

    @property
    def extra_state_attributes(self):
        if not self._is_total:
            return None
        days = (self.coordinator.data or {}).get("consumption") or []
        return {
            "history": [
                {
                    "date": datetime.fromtimestamp(d["ts"], tz=timezone.utc)
                    .date()
                    .isoformat(),
                    "total_kwh": d["total_kwh"],
                    "boost_kwh": d["boost_kwh"],
                    "offpeak_kwh": d["offpeak_kwh"],
                }
                for d in days
            ]
        }


class HoursTodaySensor(SecureE7Entity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, key: str, name: str) -> None:
        super().__init__(coordinator, key, name)
        self._key = key

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._key)


class ScheduleSensor(SecureE7Entity, SensorEntity):
    """Read-only weekly programme. State is today's ON windows; the whole week
    is in the attributes. Refreshed at startup and by secure_e7.get_schedule."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "schedule", "Schedule")

    @property
    def _schedule(self) -> dict | None:
        return (self.coordinator.data or {}).get("schedule")

    @property
    def native_value(self) -> str | None:
        sched = self._schedule
        if sched is None:
            return None
        today = sched["days"][DAYS[dt_util.now().weekday()]]
        if not today:
            return "off all day"
        return ", ".join(f"{w['on']}-{w['off']}" for w in today)

    @property
    def extra_state_attributes(self):
        sched = self._schedule
        return None if sched is None else dict(sched["days"])
