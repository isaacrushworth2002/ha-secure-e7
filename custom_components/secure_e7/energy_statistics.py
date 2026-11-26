"""Import the device's per-day kWh as external long-term statistics.

The Energy dashboard books a sensor's change to the time it is recorded, which
is wrong for a table the device fills in late. Statistics are keyed on the
row's own date instead, and the whole table is re-imported when it changes, so
a late row corrects the day it belongs to. Within the day, energy is placed in
the hours the element ran (see energy.daily_statistics).
"""
from __future__ import annotations

import logging
from datetime import datetime, tzinfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .energy import carry_stale_hours, daily_statistics, day_floor

_LOGGER = logging.getLogger(__name__)

# (suffix, name, row key)
SERIES = (
    ("energy", "Energy", "total_kwh"),
    ("energy_boost", "Energy Boost", "boost_kwh"),
    ("energy_offpeak", "Energy Off-Peak", "offpeak_kwh"),
)


def statistic_id(mac: str, suffix: str) -> str:
    return f"{DOMAIN}:{mac.replace(':', '').lower()}_{suffix}"


async def async_import_daily_energy(
    hass: HomeAssistant,
    mac: str,
    days: list[dict],
    schedule: dict | None = None,
    boosts: list[list] | None = None,
    now: float = 0.0,
    snapshots: dict | None = None,
    window_tz: tzinfo | None = None,
) -> None:
    if not days:
        return
    tz = dt_util.DEFAULT_TIME_ZONE
    recorder = get_instance(hass)

    async def recorded(stat_id: str, start: datetime, end: datetime | None) -> list[dict]:
        found = await recorder.async_add_executor_job(
            statistics_during_period, hass, start, end, {stat_id}, "hour", None, {"sum"}
        )
        return found.get(stat_id) or []

    for suffix, name, key in SERIES:
        stat_id = statistic_id(mac, suffix)
        stamps = [row["ts"] for row in days if row.get(key) is not None]
        if not stamps:
            continue
        # Split at the start of the oldest day, not at its first row now: an earlier
        # import may have placed that day's energy in an earlier hour.
        floor = day_floor(min(stamps), tz)
        earlier = await recorded(stat_id, dt_util.utc_from_timestamp(0), floor)
        base = (earlier or [{}])[-1].get("sum") or 0.0
        later = await recorded(stat_id, floor, None)
        rows = carry_stale_hours(
            daily_statistics(days, key, base, tz, schedule, boosts, now, snapshots, window_tz),
            base,
            [dt_util.utc_from_timestamp(row["start"]) for row in later],
        )
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Secure E7 {name}",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
        async_add_external_statistics(
            hass, metadata, [StatisticData(**row) for row in rows]
        )
    _LOGGER.debug("secure_e7 imported %d days of energy statistics", len(days))
