import asyncio
from datetime import datetime, timezone

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
    assert c._derive({"hot_water_on": False})["estimated_power"] == 0
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


def test_learned_power_comes_back_after_a_restart(coordinator_env):
    c = make_coordinator(coordinator_env)
    c._learn_power(ROWS)
    later = make_coordinator(coordinator_env)
    later._power_store.saved = c._power_store.saved
    asyncio.run(later._load_power())
    assert later._derive({"hot_water_on": True})["estimated_power"] == 2956
