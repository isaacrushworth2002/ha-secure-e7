"""Import the device's per-day kWh as external long-term statistics.

The Energy dashboard books a sensor's change to the time it is recorded, which
is wrong for a table the device fills in late. Statistics are keyed on the
row's own date instead, and the whole table is re-imported when it changes, so
a late row corrects the day it belongs to.
"""
from __future__ import annotations

import logging

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
from .energy import daily_statistics

_LOGGER = logging.getLogger(__name__)

# (suffix, name, row key)
SERIES = (
    ("energy", "Energy", "total_kwh"),
    ("energy_boost", "Energy Boost", "boost_kwh"),
    ("energy_offpeak", "Energy Off-Peak", "offpeak_kwh"),
)


def statistic_id(mac: str, suffix: str) -> str:
    return f"{DOMAIN}:{mac.replace(':', '').lower()}_{suffix}"


async def async_import_daily_energy(hass: HomeAssistant, mac: str, days: list[dict]) -> None:
    if not days:
        return
    first = daily_statistics(days, "total_kwh")
    if not first:
        return
    first_start = first[0]["start"]
    for suffix, name, key in SERIES:
        stat_id = statistic_id(mac, suffix)
        earlier = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            dt_util.utc_from_timestamp(0),
            first_start,
            {stat_id},
            "hour",
            None,
            {"sum"},
        )
        base = (earlier.get(stat_id) or [{}])[-1].get("sum") or 0.0
        rows = daily_statistics(days, key, base)
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
