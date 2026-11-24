"""Weekly hot-water programme: device reply -> per-day ON windows.

Read format confirmed against a real unit and its console on 2026-10-18:
42 slots (7 days x 6, Monday first), each slot {"O": minute-of-day, "T": state},
as three on/off pairs per day. Unused windows are stored as zero-length pairs
(for example 12:00 on, 12:00 off) and are dropped.

Kept free of Home Assistant imports so it can be unit tested on its own.
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SLOTS_PER_DAY = 6
K_OFFSET = "O"  # minute of day the slot starts
K_STATE = "T"   # 1 = on, 0 = off
UNUSED = 65535  # 0xFFFF, same "no time" sentinel as MAX_TIME in const.py
END_OF_DAY = 24 * 60
WINDOWS_PER_DAY = SLOTS_PER_DAY // 2  # device limit: 3 ON windows a day
GRID_MINUTES = 15
# Unused windows are zero-length pairs. The console puts them at 12:00; we do too,
# or at the last real off time if later, so slot times never go backwards.
SPARE_AT = 12 * 60


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _windows(day_slots: list[dict]) -> list[dict]:
    windows: list[dict] = []
    on_at: int | None = None
    for slot in day_slots:
        minute = slot.get(K_OFFSET)
        if minute is None or minute == UNUSED:
            continue
        state = slot.get(K_STATE)
        if state and on_at is None:
            on_at = minute
        elif not state and on_at is not None:
            if minute != on_at:
                windows.append({"on": _hhmm(on_at), "off": _hhmm(minute)})
            on_at = None
    if on_at is not None:
        windows.append({"on": _hhmm(on_at), "off": _hhmm(END_OF_DAY)})
    return windows


def _find_slots(node) -> list[dict] | None:
    """Depth-first search for the first list of slot dicts anywhere in the reply."""
    if isinstance(node, list):
        if node and all(isinstance(x, dict) and K_OFFSET in x for x in node):
            return node
        children = node
    elif isinstance(node, dict):
        children = list(node.values())
    else:
        return None
    for child in children:
        found = _find_slots(child)
        if found is not None:
            return found
    return None


def read_slots(obj: dict) -> list[dict] | None:
    """The 42 raw device slots in a reply, or None if it does not hold a programme."""
    if not isinstance(obj, dict):
        return None
    slots = _find_slots(obj.get("R"))
    if slots is None or len(slots) != len(DAYS) * SLOTS_PER_DAY:
        return None
    if not all(isinstance(slot.get(K_OFFSET), int) for slot in slots):
        return None
    return slots


def parse_schedule(obj: dict) -> dict | None:
    """Return {"days": {monday: [{"on": "HH:MM", "off": "HH:MM"}, ...], ...}},
    or None if the reply does not hold a 42-slot programme."""
    slots = read_slots(obj)
    if slots is None:
        return None
    return {
        "days": {
            day: _windows(slots[i * SLOTS_PER_DAY : (i + 1) * SLOTS_PER_DAY])
            for i, day in enumerate(DAYS)
        }
    }


def _minute(text) -> int:
    try:
        hh, mm = str(text).split(":")
        hour, minute = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"time {text!r} is not HH:MM (quote times in YAML)") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"time {text!r} is not HH:MM (latest is 23:45)")
    if minute % GRID_MINUTES:
        raise ValueError(f"time {text!r} is not on the 15-minute grid")
    return hour * 60 + minute


def _day_slots(day: str, windows) -> list[dict]:
    if len(windows) > WINDOWS_PER_DAY:
        raise ValueError(f"{day}: at most {WINDOWS_PER_DAY} ON windows a day, got {len(windows)}")
    pairs = []
    for window in windows:
        if not isinstance(window, dict) or "on" not in window or "off" not in window:
            raise ValueError(f"{day}: each window needs on and off, like {{'on': '02:15', 'off': '06:00'}}")
        on, off = _minute(window["on"]), _minute(window["off"])
        if on >= off:
            raise ValueError(f"{day}: on {window['on']} must be before off {window['off']}")
        pairs.append((on, off))
    pairs.sort()
    for (prev_on, prev_off), (on, off) in zip(pairs, pairs[1:]):
        if on <= prev_off:
            raise ValueError(
                f"{day}: windows {_hhmm(prev_on)}-{_hhmm(prev_off)} and {_hhmm(on)}-{_hhmm(off)} "
                "overlap or touch; join them into one"
            )
    spare = max([SPARE_AT] + [off for _, off in pairs])
    pairs += [(spare, spare)] * (WINDOWS_PER_DAY - len(pairs))
    return [slot for on, off in pairs for slot in ({K_OFFSET: on, K_STATE: 1}, {K_OFFSET: off, K_STATE: 0})]


def encode_days(days: dict) -> dict[str, list[dict]]:
    """Turn {day: [{"on": "HH:MM", "off": "HH:MM"}, ...]} into each day's 6
    device slots, enforcing the device limits. Raises ValueError with a reason."""
    unknown = [day for day in days if day not in DAYS]
    if unknown:
        raise ValueError(f"unknown days: {', '.join(map(str, unknown))}")
    return {day: _day_slots(day, windows) for day, windows in days.items()}


def splice(base_slots: list[dict], encoded: dict[str, list[dict]]) -> list[dict]:
    """Replace the encoded days in a 42-slot programme; other days keep their
    device slots exactly as read."""
    slots = list(base_slots)
    for i, day in enumerate(DAYS):
        if day in encoded:
            slots[i * SLOTS_PER_DAY : (i + 1) * SLOTS_PER_DAY] = encoded[day]
    return slots


def encode_schedule(days: dict) -> list[dict]:
    """All 42 device slots for a full week. Raises ValueError with a reason."""
    missing = [day for day in DAYS if day not in days]
    if missing:
        raise ValueError(f"missing days: {', '.join(missing)}")
    encoded = encode_days(days)
    return [slot for day in DAYS for slot in encoded[day]]


def unit_timezone(tz: tzinfo, follows_dst: bool | None, year: int) -> tzinfo:
    """The zone the unit's clock keeps, for a home in `tz`.

    With daylight saving switched off on the unit it stays on standard time.
    """
    if follows_dst is not False:
        return tz
    return timezone(min(datetime(year, month, 1, tzinfo=tz).utcoffset() for month in (1, 7)))


def is_on_at(windows: list[dict], minute: int) -> bool:
    """Whether a day's windows have the programme on at that minute of the day."""
    return any(_minute_of(w["on"]) <= minute < _minute_of(w["off"]) for w in windows)


def next_change(days: dict, weekday: int, minute: int) -> tuple[str, str] | None:
    """Next change in whether the programme is on after (weekday 0=Monday, minute
    of day), as ("HH:MM", "on" | "off"), looking up to a week ahead.

    Windows that touch across midnight are one run, so no change is reported at
    the join. None if the week is empty or on all the time. Times are the unit's
    wall clock, so a clock change does not move them.
    """
    horizon = 2 * len(DAYS) * END_OF_DAY
    runs: list[list[int]] = []
    for day_index in range(2 * len(DAYS)):
        base = day_index * END_OF_DAY
        name = DAYS[(weekday + day_index) % len(DAYS)]
        for window in sorted(days.get(name, []), key=lambda w: _minute_of(w["on"])):
            start, end = base + _minute_of(window["on"]), base + _minute_of(window["off"])
            if runs and start <= runs[-1][1]:
                runs[-1][1] = max(runs[-1][1], end)
            else:
                runs.append([start, end])
    now = minute
    edges = sorted(
        (at, edge)
        for start, end in runs
        for at, edge in ((start, "on"), (end, "off"))
        if now < at < horizon
    )
    if not edges:
        return None
    at, edge = edges[0]
    return _hhmm(at % END_OF_DAY), edge


def _minute_of(hhmm: str) -> int:
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)
