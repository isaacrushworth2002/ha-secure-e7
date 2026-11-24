"""Turn a GetAllServiceValues reply into state, without blanking what it omits."""
from __future__ import annotations

from .const import (
    CH_DEVICE_LOCK,
    CH_DST,
    CH_HOTWATER_STATE,
    CH_MODE,
    CH_NEXT_PERIOD,
    CH_NEXT_SETPOINT,
    CH_TIMED_BOOST,
    CH_TIMER_STATE,
    MAX_TIME,
    OT_ADVANCE,
    SI_HOTWATER,
    SI_TIMER,
)

# A poll that cannot give these is a failed poll, not an unknown state.
REQUIRED = ("hot_water_on", "mode")
# Read from other channels, so a reply that leaves them out can reuse the last value.
CARRIED = ("mode", "timed_boost")


class IncompleteStatus(ValueError):
    """The reply did not carry a value the entities need."""


def parse_status(obj: dict) -> tuple[dict, int | None]:
    """Return the state in the reply and the hot-water channel id, if it was sent."""
    res = obj.get("R") or {}
    channels = res.get("V", []) if isinstance(res, dict) else []
    hw_boi = None
    state: dict = {
        "hot_water_on": None,
        "mode": None,
        "timed_boost": None,
        "boost_minutes_remaining": None,
        "next_change_time": None,
        "next_change_state": None,
        "panel_lock": None,
        "dst": None,
        "timer_state": None,
    }
    for ch in channels:
        si = ch.get("SI")
        chars = {c.get("I"): c for c in ch.get("V", [])}
        if si == SI_HOTWATER:
            hw_boi = ch.get("I")
            hw = chars.get(CH_HOTWATER_STATE)
            if hw is not None:
                state["hot_water_on"] = bool(hw.get("V"))
                if hw.get("OT") == OT_ADVANCE and hw.get("D"):
                    state["boost_minutes_remaining"] = hw.get("D")
            sch = chars.get(CH_TIMED_BOOST)
            if sch is not None:
                state["timed_boost"] = bool(sch.get("V"))
            nxt = chars.get(CH_NEXT_PERIOD)
            if nxt is not None:
                v = nxt.get("V")
                if v is not None and v != MAX_TIME:
                    state["next_change_time"] = f"{v // 60:02d}:{v % 60:02d}"
            nss = chars.get(CH_NEXT_SETPOINT)
            if nss is not None and nss.get("V") is not None:
                state["next_change_state"] = "on" if nss.get("V") else "off"
        if si == SI_TIMER:
            timer = chars.get(CH_TIMER_STATE)
            if timer is not None:
                state["timer_state"] = timer.get("V")
            for key, ch_id in (("panel_lock", CH_DEVICE_LOCK), ("dst", CH_DST)):
                c = chars.get(ch_id)
                if c is not None and c.get("V") is not None:
                    state[key] = bool(c.get("V"))
        if CH_MODE in chars and state["mode"] is None:
            state["mode"] = chars[CH_MODE].get("V")
    return state, hw_boi


def raw_characteristics(obj: dict) -> dict:
    """Every characteristic in the reply as sent, keyed "service.object.characteristic"."""
    res = obj.get("R") or {}
    channels = res.get("V", []) if isinstance(res, dict) else []
    return {
        f"{ch.get('SI')}.{ch.get('I')}.{c.get('I')}": {k: v for k, v in c.items() if k != "I"}
        for ch in channels
        for c in ch.get("V", [])
    }


def complete_status(state: dict, last: dict | None) -> dict:
    """Fill mode and timed_boost from the last state; raise if any required value is missing."""
    for key in CARRIED:
        if state[key] is None and last and last.get(key) is not None:
            state[key] = last[key]
    missing = [key for key in REQUIRED if state[key] is None]
    if missing:
        raise IncompleteStatus("reply is missing " + ", ".join(missing))
    return state
