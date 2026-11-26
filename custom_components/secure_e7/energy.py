"""Energy bookkeeping that needs no Home Assistant, so it can be unit tested."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo

from .consumption import SECONDS_PER_DAY, row_date
from .schedule import DAYS


KEEP_DAYS = 40  # retention of the boost log and the programme snapshots
MAX_BOOST = 3 * 3600  # seconds; the longest boost offered is 2 hours


def gap_energy_wh(on: bool | None, watts: float, elapsed: float, max_gap: float) -> float:
    """Wh to credit for the time since the last successful poll.

    A longer gap means polls failed, so we do not know the heater was on.
    """
    if not on or elapsed < 0 or elapsed > max_gap:
        return 0.0
    return watts * elapsed / 3600.0


def day_start(ts: int, tz: tzinfo = timezone.utc) -> datetime:
    """Start of a row's day in UTC, on the hour as statistics require.

    A row stamped at UTC midnight starts then. Any other stamp is taken to be
    local midnight (as in consumption.row_date), so the day starts there.
    """
    day = row_date(ts, tz)
    if ts % SECONDS_PER_DAY == 0:
        return datetime.combine(day, time(), tzinfo=timezone.utc)
    start = datetime.combine(day, time(), tzinfo=tz).astimezone(timezone.utc)
    return start.replace(minute=0, second=0, microsecond=0)


def _local(day: date, minute: int, tz: tzinfo) -> datetime:
    """UTC instant of a wall-clock minute of the day, so DST days keep local meaning."""
    wall = datetime.combine(day, time(), tzinfo=tz) + timedelta(minutes=minute)
    return wall.astimezone(timezone.utc)


def day_floor(ts: int, tz: tzinfo = timezone.utc) -> datetime:
    """The earliest hour any of a row's energy can be placed in."""
    midnight = _local(row_date(ts, tz), 0, tz).replace(minute=0, second=0, microsecond=0)
    return min(midnight, day_start(ts, tz))


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def offpeak_spans(windows: list[dict], day: date, minutes: float, tz: tzinfo) -> list[tuple]:
    """Where the element ran: `minutes` filled into the day's windows from the first on.

    The thermostat has the element on from the start of the window until the
    cylinder is hot, so the run is taken as contiguous from the window start.
    """
    spans = []
    left = timedelta(minutes=minutes)
    for window in sorted(windows, key=lambda w: _minutes(w["on"])):
        start = _local(day, _minutes(window["on"]), tz)
        end = _local(day, _minutes(window["off"]), tz)
        take = min(left, end - start)
        if take > timedelta(0):
            spans.append((start, start + take))
            left -= take
    return spans


def note_boost(log: list[list], active: bool, now: float, keep: float = KEEP_DAYS * SECONDS_PER_DAY) -> bool:
    """Open or close the boost record from a poll. True if the log changed.

    An entry is [start, end], end None while the boost runs.
    """
    open_entry = bool(log) and log[-1][1] is None
    if active and not open_entry:
        log.append([now, None])
    elif not active and open_entry:
        log[-1][1] = now
    else:
        return False
    log[:] = [e for e in log if (e[1] is None or e[1] > now - keep)]
    return True


def boost_spans(boosts: list[list], day: date, tz: tzinfo, now: float) -> list[tuple]:
    """The recorded boosts that overlap the local day, cut at its edges.

    No boost runs longer than MAX_BOOST, so a record left open across a restart
    cannot claim more than that.
    """
    lo, hi = _local(day, 0, tz), _local(day, 24 * 60, tz)
    spans = []
    for start, end in boosts:
        a = max(datetime.fromtimestamp(start, timezone.utc), lo)
        stop = min(now if end is None else end, start + MAX_BOOST)
        b = min(datetime.fromtimestamp(stop, timezone.utc), hi)
        if b > a:
            spans.append((a, b))
    return spans


def spread(kwh: float, spans: list[tuple]) -> dict[datetime, float]:
    """`kwh` split across the UTC hours the spans cover, in proportion to the seconds in each."""
    seconds: dict[datetime, float] = {}
    for start, end in spans:
        hour = start.replace(minute=0, second=0, microsecond=0)
        while hour < end:
            nxt = hour + timedelta(hours=1)
            seconds[hour] = seconds.get(hour, 0.0) + (min(end, nxt) - max(start, hour)).total_seconds()
            hour = nxt
    total = sum(seconds.values())
    if total <= 0:
        return {}
    return {hour: kwh * share / total for hour, share in seconds.items()}


def _add(into: dict, parts: dict) -> None:
    for hour, kwh in parts.items():
        into[hour] = into.get(hour, 0.0) + kwh


def daily_statistics(
    days: list[dict],
    key: str,
    base: float = 0.0,
    tz: tzinfo = timezone.utc,
    schedule: dict | None = None,
    boosts: list[list] | None = None,
    now: float = 0.0,
    snapshots: dict[str, list[dict]] | None = None,
    window_tz: tzinfo | None = None,
) -> list[dict]:
    """Cumulative hourly rows for external statistics.

    The device only meters whole days. Off-peak kWh is spread over the hours the
    element ran (offpeak_spans, from the day's windows in `schedule`, a
    {weekday name: [{"on", "off"}]} map, and the actual minutes), or from
    `snapshots`, the windows a date had when it was recorded ({ISO date: windows}),
    which win over `schedule` so a later edit does not move past days, and boost kWh
    over the boosts recorded in `boosts` ([start, end] timestamps). Anything that
    cannot be placed goes in the first hour of the day (see day_start and
    consumption.row_date for `tz`). `base` is the cumulative sum already recorded
    before the first row. The windows are read in `window_tz`, the unit's clock,
    when it differs from `tz`.
    """
    by_day: dict = {}
    for row in days:
        if row.get(key) is not None:
            by_day[row_date(row["ts"], tz)] = row
    hours: dict[datetime, float] = {}
    for day, row in by_day.items():
        windows = (snapshots or {}).get(day.isoformat())
        if windows is None:
            windows = (schedule or {}).get(DAYS[day.weekday()]) or []
        placed: dict[datetime, float] = {}
        if key != "boost_kwh":
            minutes = round(float(row.get("offpeak_actual_h") or 0.0) * 60)
            offpeak = float(row.get("offpeak_kwh") or 0.0) if key == "total_kwh" else float(row[key])
            _add(placed, spread(offpeak, offpeak_spans(windows, day, minutes, window_tz or tz)))
        if key != "offpeak_kwh":
            boost = float(row.get("boost_kwh") or 0.0) if key == "total_kwh" else float(row[key])
            _add(placed, spread(boost, boost_spans(boosts or [], day, tz, now)))
        value = float(row[key])
        if value >= 0 and sum(placed.values()) > value:
            scale = value / sum(placed.values())
            placed = {hour: kwh * scale for hour, kwh in placed.items()}
        rest = value - sum(placed.values())
        first = day_start(row["ts"], tz)
        if abs(rest) > 1e-9 or not placed:
            placed[first] = placed.get(first, 0.0) + rest
        _add(hours, placed)
    total = base
    rows = []
    for start in sorted(hours):
        total += hours[start]
        rows.append({"start": start, "sum": round(total, 3), "state": round(total, 3)})
    return rows


def carry_stale_hours(rows: list[dict], base: float, stale_starts: list[datetime]) -> list[dict]:
    """`rows` plus a flat row for each hour that has an old statistic but no row now.

    Statistics cannot be deleted, so an hour the table no longer has is kept
    consistent by holding the running sum, as if nothing was used that day.
    """
    known = {row["start"] for row in rows}
    extra = []
    for start in sorted(set(stale_starts) - known):
        before = [row for row in rows if row["start"] < start]
        total = before[-1]["sum"] if before else base
        extra.append({"start": start, "sum": total, "state": total})
    return sorted(rows + extra, key=lambda row: row["start"])
