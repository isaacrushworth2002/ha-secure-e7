"""DynamicTariff consumption rows -> per-day figures, without Home Assistant."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone, tzinfo

from .const import (
    CENTI_AH_TO_KWH,
    K_BOOST_ACTUAL_MIN,
    K_BOOST_CHARGE,
    K_BOOST_SCHED_MIN,
    K_DTO_DAYS,
    K_DTO_ID,
    K_OFFPEAK_ACTUAL_MIN,
    K_OFFPEAK_CHARGE,
    K_OFFPEAK_SCHED_MIN,
    K_TIME,
)

SECONDS_PER_DAY = 86400


def _number(value) -> float:
    """A finite, non-negative number, else 0, so one bad field cannot drop the reply."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _hours(minutes) -> float:
    return round(_number(minutes) / 60.0, 3)


def parse_consumption(obj: dict) -> list[dict]:
    """DynamicTariff getConsumptionState reply -> list of per-day dicts.

    R = [ {"I": channelId, "D": [ {"T":unix,"OP":f,"BP":f,"OA":f,"BA":f,"OS":f,"BS":f} ] } ]
    kWh = charge_centi_ah * 230 / 100000 (mirrors ConsumptionProfile.java).
    """
    result = obj.get("R") if isinstance(obj, dict) else None
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        return []
    days: list[dict] = []
    for dto in result:
        if not isinstance(dto, dict):
            continue
        rows = dto.get(K_DTO_DAYS)
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            t = int(_number(row.get(K_TIME)))
            if t == 0:
                continue
            bp = _number(row.get(K_BOOST_CHARGE))
            op = _number(row.get(K_OFFPEAK_CHARGE))
            days.append(
                {
                    "channel_id": dto.get(K_DTO_ID),
                    "ts": t,
                    "boost_kwh": round(bp * CENTI_AH_TO_KWH, 3),
                    "offpeak_kwh": round(op * CENTI_AH_TO_KWH, 3),
                    "total_kwh": round((bp + op) * CENTI_AH_TO_KWH, 3),
                    "boost_charge_centi_ah": bp,
                    "offpeak_charge_centi_ah": op,
                    "boost_actual_h": _hours(row.get(K_BOOST_ACTUAL_MIN)),
                    "offpeak_actual_h": _hours(row.get(K_OFFPEAK_ACTUAL_MIN)),
                    "boost_scheduled_h": _hours(row.get(K_BOOST_SCHED_MIN)),
                    "offpeak_scheduled_h": _hours(row.get(K_OFFPEAK_SCHED_MIN)),
                }
            )
    days.sort(key=lambda d: d["ts"])
    return days


def row_date(ts: int, tz: tzinfo = timezone.utc) -> date:
    """The calendar day a row belongs to.

    Rows are stamped at UTC midnight. A stamp that is not on a UTC midnight is
    taken to be local midnight, so it is read in `tz`, not UTC, to stay on its day.
    """
    if ts % SECONDS_PER_DAY == 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    return datetime.fromtimestamp(ts, tz=tz).date()


def latest_row(days: list[dict]) -> dict:
    """The newest row, or an empty dict."""
    return days[-1] if days else {}
