"""Weekly hot-water programme: device reply -> per-day ON windows.

Read format confirmed against a real unit and its console on 2026-10-18:
42 slots (7 days x 6, Monday first), each slot {"O": minute-of-day, "T": state},
as three on/off pairs per day. Unused windows are stored as zero-length pairs
(for example 12:00 on, 12:00 off) and are dropped.

Kept free of Home Assistant imports so it can be unit tested on its own.
"""
from __future__ import annotations

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
        raise ValueError(f"time {text!r} is not HH:MM") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"time {text!r} is not HH:MM")
    if minute % GRID_MINUTES:
        raise ValueError(f"time {text!r} is not on the 15-minute grid")
    return hour * 60 + minute


def _day_slots(day: str, windows) -> list[dict]:
    if len(windows) > WINDOWS_PER_DAY:
        raise ValueError(f"{day}: at most {WINDOWS_PER_DAY} ON windows a day")
    pairs = []
    for window in windows:
        if not isinstance(window, dict) or "on" not in window or "off" not in window:
            raise ValueError(f"{day}: each window needs on and off")
        on, off = _minute(window["on"]), _minute(window["off"])
        if on >= off:
            raise ValueError(f"{day}: on {window['on']} must be before off {window['off']}")
        pairs.append((on, off))
    pairs.sort()
    for (_, prev_off), (on, _) in zip(pairs, pairs[1:]):
        if on <= prev_off:
            raise ValueError(f"{day}: windows overlap or touch")
    spare = max([SPARE_AT] + [off for _, off in pairs])
    pairs += [(spare, spare)] * (WINDOWS_PER_DAY - len(pairs))
    return [slot for on, off in pairs for slot in ({K_OFFSET: on, K_STATE: 1}, {K_OFFSET: off, K_STATE: 0})]


def encode_days(days: dict) -> dict[str, list[dict]]:
    """Turn {day: [{"on": "HH:MM", "off": "HH:MM"}, ...]} into each day's 6
    device slots, enforcing the device limits. Raises ValueError with a reason."""
    unknown = [day for day in days if day not in DAYS]
    if unknown:
        raise ValueError(f"unknown days: {', '.join(unknown)}")
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


def next_change(days: dict, weekday: int, minute: int) -> tuple[str, str] | None:
    """Next programme edge after (weekday 0=Monday, minute of day), as
    ("HH:MM", "on" | "off"), looking up to a week ahead. None if the week is empty."""
    for offset in range(len(DAYS) + 1):
        day = DAYS[(weekday + offset) % len(DAYS)]
        edges = sorted(
            (_minute_of(w[edge]), edge)
            for w in days.get(day, [])
            for edge in ("on", "off")
        )
        for at, edge in edges:
            if offset or at > minute:
                return _hhmm(at), edge
    return None


def _minute_of(hhmm: str) -> int:
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)
