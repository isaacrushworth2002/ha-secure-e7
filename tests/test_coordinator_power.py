import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from conftest import make_coordinator

ROWS = [
    {
        "ts": int(datetime(2026, 11, 21, tzinfo=timezone.utc).timestamp()),
        "offpeak_kwh": 7.144,
        "offpeak_actual_h": 2.417,
        "boost_kwh": 0.0,
        "boost_actual_h": 0.0,
    }
]


def test_power_is_unknown_while_on_until_a_day_has_been_metered(coordinator_env):
    c = make_coordinator(coordinator_env)
    state = c._derive({"hot_water_on": True})
    assert state["estimated_power"] is None
    assert state["power_info"]["rating_source"] == "none"
    off = {"hot_water_on": False, "mode": 0}
    assert c._derive(off)["estimated_power"] == 0
    c._learn_power(ROWS)
    state = c._derive({"hot_water_on": True})
    assert state["estimated_power"] == 2956
    assert state["power_info"]["rating_source"] == "learned_offpeak"


def test_a_configured_rating_overrides_what_was_learned(coordinator_env):
    c = make_coordinator(coordinator_env, rating_watts=2000)
    c._learn_power(ROWS)
    state = c._derive({"hot_water_on": True})
    assert state["estimated_power"] == 2000
    assert state["power_info"]["rating_source"] == "configured"
    assert state["power_info"]["learned_offpeak_watts"] == 2956


def at(env, hour, minute=0):
    env.module.dt_util.now = lambda: datetime(2026, 11, 21, hour, minute, tzinfo=timezone.utc)


def state(**changes):
    schedule = {"days": {"saturday": [{"on": "02:15", "off": "06:00"}]}}
    return {"hot_water_on": False, "mode": 2, "timer_state": 0, "schedule": schedule, **changes}


def test_the_off_peak_run_shows_power_although_hot_water_reads_off(coordinator_env):
    c = make_coordinator(coordinator_env)
    c._learn_power(ROWS)
    at(coordinator_env, 3)
    data = c._derive(state())
    assert data["heating"] is True and data["estimated_power"] == 2956
    assert data["power_info"]["basis"] == "programme"
    assert data["power_info"]["thermostat_may_have_cut_out"] is True
    at(coordinator_env, 12)
    data = c._derive(state())
    assert data["heating"] is False and data["estimated_power"] == 0


def test_a_unit_without_daylight_saving_runs_an_hour_later_in_summer(coordinator_env):
    zone = ZoneInfo("Europe/London")
    coordinator_env.module.dt_util.DEFAULT_TIME_ZONE = zone
    coordinator_env.module.dt_util.now = lambda: datetime(2026, 7, 4, 2, 30, tzinfo=zone)
    c = make_coordinator(coordinator_env)
    assert c._derive(state(dst=True))["heating"] is True
    assert c._derive(state(dst=False))["heating"] is False


def test_a_published_write_works_the_state_out_again(coordinator_env):
    c = make_coordinator(coordinator_env)
    at(coordinator_env, 3)
    c._publish(state())
    assert c.data["heating"] is True
    c._publish({**c.data, "mode": 0})
    assert c.data["heating"] is False


def metered(env, monkeypatch, c):
    """A function that polls once a minute for `minutes` and integrates the energy."""
    clock = [0.0]
    monkeypatch.setattr(env.module.time, "monotonic", lambda: clock[0])
    c._integrate_energy(c._derive(state()))

    def run(minutes, **changes):
        for _ in range(minutes):
            clock[0] += 60
            c._integrate_energy(c._derive(state(**changes)))

    return run


def test_off_peak_energy_stops_at_the_usual_run_time(coordinator_env, monkeypatch):
    c = make_coordinator(coordinator_env)
    c._learn_power(ROWS)
    at(coordinator_env, 3)
    run = metered(coordinator_env, monkeypatch, c)
    run(60)
    assert c._energy_wh == pytest.approx(2956)
    run(180)
    assert c._energy_wh == pytest.approx(2956 * 2.417)
    run(60, hot_water_on=True)
    assert c._energy_wh == pytest.approx(2956 * 3.417)


def test_no_energy_is_credited_while_the_power_is_unknown(coordinator_env, monkeypatch):
    c = make_coordinator(coordinator_env)
    at(coordinator_env, 3)
    metered(coordinator_env, monkeypatch, c)(10)
    assert c._energy_wh == 0


def test_raw_values_are_recorded_only_when_they_change(coordinator_env, caplog):
    c = make_coordinator(coordinator_env)
    value = [0]

    async def request(hi, si, args=None):
        channels = [
            {"SI": 33, "I": 1, "V": [{"I": 6, "V": 2}, {"I": 5, "V": value[0]}]},
            {"SI": 16, "I": 2, "V": [{"I": 4, "V": 0}]},
        ]
        return {"R": {"V": channels}}

    c._request = request
    caplog.set_level("DEBUG")
    asyncio.run(c._poll())
    asyncio.run(c._poll())
    value[0] = 1
    data = asyncio.run(c._poll())
    assert data["timer_state"] == 1 and data["characteristics"]["33.1.5"] == {"V": 1}
    assert len(c.characteristic_changes) == 2
    assert c.characteristic_changes[-1]["changed"] == {"33.1.5": [{"V": 0}, {"V": 1}]}
    assert caplog.text.count("characteristics changed") == 2


def test_learned_power_comes_back_after_a_restart(coordinator_env):
    c = make_coordinator(coordinator_env)
    c._learn_power(ROWS)
    later = make_coordinator(coordinator_env)
    later._power_store.saved = c._power_store.saved
    asyncio.run(later._load_power())
    assert later._derive({"hot_water_on": True})["estimated_power"] == 2956
