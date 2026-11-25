"""Element power and on-state, without Home Assistant.

The unit reports no live power. Each metered day gives energy and the minutes
the element actually ran, for off-peak and boost separately (a two-channel
unit may drive two different elements).
"""
from __future__ import annotations

from datetime import timezone, tzinfo
from statistics import median

from .const import MODE_HOME, MODE_OFF
from .consumption import row_date
from .schedule import is_on_at

PROGRAMMES = ("offpeak", "boost")
# Run time is whole minutes, so shorter runs give too coarse a figure.
MIN_RUN_HOURS = 0.4
KEEP_SAMPLES = 14  # newest qualifying days per programme


def load_samples(saved) -> dict:
    """Stored samples, {programme: {ISO date: [kWh, hours]}}, dropping anything malformed."""
    samples: dict = {name: {} for name in PROGRAMMES}
    if not isinstance(saved, dict):
        return samples
    for name in PROGRAMMES:
        days = saved.get(name)
        for day, value in (days.items() if isinstance(days, dict) else ()):
            if (
                isinstance(value, list)
                and len(value) == 2
                and all(isinstance(x, (int, float)) and x > 0 for x in value)
            ):
                samples[name][day] = list(value)
    return samples


def update_samples(samples: dict, rows: list[dict], tz: tzinfo = timezone.utc) -> bool:
    """Add the rows that ran long enough to measure. True if the samples changed."""
    changed = False
    for name in PROGRAMMES:
        days = dict(samples[name])
        for row in rows:
            kwh = row.get(f"{name}_kwh") or 0
            hours = row.get(f"{name}_actual_h") or 0
            if kwh > 0 and hours >= MIN_RUN_HOURS:
                days[row_date(row["ts"], tz).isoformat()] = [kwh, hours]
        days = {day: days[day] for day in sorted(days)[-KEEP_SAMPLES:]}
        if days != samples[name]:
            samples[name] = days
            changed = True
    return changed


def learned_watts(samples: dict, programme: str) -> int | None:
    """The median day, so one odd row does not move the figure."""
    days = samples[programme].values()
    return round(median(kwh * 1000 / hours for kwh, hours in days)) if days else None


def typical_run_hours(samples: dict, programme: str) -> float | None:
    days = samples[programme].values()
    return round(median(hours for _, hours in days), 3) if days else None


def heating_state(
    hot_water_on: bool | None,
    timer_state: int | None,
    mode: int | None,
    windows: list[dict] | None,
    minute: int,
) -> tuple[bool | None, str]:
    """(whether the element is switched on, what says so).

    On means powered: the thermostat may still have cut the element out, which
    the unit does not report. The timer state is the unit's own on/off for the
    off-peak channel; `windows`, today's off-peak programme (None if not read),
    stands in when a reply has no timer state. None means it cannot be told.
    """
    if hot_water_on:
        return True, "hot_water"
    if mode == MODE_OFF:
        return False, "power_off"
    if timer_state in (0, 1):
        return bool(timer_state), "timer_state"
    if windows is None:
        return None, "unknown"
    if not is_on_at(windows, minute):
        return False, "idle"
    # What Away does to the programme is unknown.
    return (True, "programme") if mode == MODE_HOME else (None, "unknown")


def element_power(programme: str, configured: int | None, samples: dict) -> tuple[int | None, str]:
    """(watts, source). A configured value wins; otherwise the programme's own
    figure, or the other programme's until it has one. Never a made-up default.
    """
    if configured:
        return configured, "configured"
    other = PROGRAMMES[1 - PROGRAMMES.index(programme)]
    for name in (programme, other):
        watts = learned_watts(samples, name)
        if watts is not None:
            return watts, f"learned_{name}"
    return None, "none"
