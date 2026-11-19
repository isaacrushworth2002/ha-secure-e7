"""Energy bookkeeping that needs no Home Assistant, so it can be unit tested."""
from __future__ import annotations

from datetime import datetime, time, timezone, tzinfo

from .consumption import row_date


def gap_energy_wh(on: bool | None, watts: float, elapsed: float, max_gap: float) -> float:
    """Wh to credit for the time since the last successful poll.

    A longer gap means polls failed, so we do not know the heater was on.
    """
    if not on or elapsed < 0 or elapsed > max_gap:
        return 0.0
    return watts * elapsed / 3600.0


def daily_statistics(
    days: list[dict], key: str, base: float = 0.0, tz: tzinfo = timezone.utc
) -> list[dict]:
    """Cumulative rows for external statistics, one per device day.

    The device only meters whole days, so each day's kWh is placed in the hour
    starting 00:00 UTC of the row's date (see consumption.row_date for `tz`). `base` is the cumulative sum already
    recorded before the first row.
    """
    by_day: dict = {}
    for row in days:
        value = row.get(key)
        if value is None:
            continue
        by_day[row_date(row["ts"], tz)] = float(value)
    total = base
    rows = []
    for day in sorted(by_day):
        total = round(total + by_day[day], 3)
        rows.append(
            {
                "start": datetime.combine(day, time(), tzinfo=timezone.utc),
                "sum": total,
                "state": total,
            }
        )
    return rows
