import pytest

from conftest import load

status = load("status")
const = load("const")


def reply(hot_water=True, mode=True, schedule=True, boost=None, settings=None):
    hw_chars = []
    if hot_water:
        char = {"I": const.CH_HOTWATER_STATE, "V": 1}
        if boost:
            char.update(OT=const.OT_ADVANCE, D=boost)
        hw_chars.append(char)
    if schedule:
        hw_chars.append({"I": const.CH_TIMED_BOOST, "V": 1})
    hw_chars.append({"I": const.CH_NEXT_PERIOD, "V": 390})
    hw_chars.append({"I": const.CH_NEXT_SETPOINT, "V": 0})
    channels = [{"SI": const.SI_HOTWATER, "I": 2, "V": hw_chars}]
    if mode:
        channels.append({"SI": const.SI_THERMOSTAT, "I": 1, "V": [{"I": const.CH_MODE, "V": 2}]})
    if settings is not None:
        channels.append(
            {"SI": const.SI_TIMER, "I": 1, "V": [{"I": i, "V": v} for i, v in settings.items()]}
        )
    return {"R": {"V": channels}}


def test_full_reply_parses():
    state, boi = status.parse_status(reply(boost=30))
    assert boi == 2
    assert state["hot_water_on"] is True
    assert state["mode"] == 2
    assert state["timed_boost"] is True
    assert state["boost_minutes_remaining"] == 30
    assert state["next_change_time"] == "06:30"
    assert state["next_change_state"] == "off"
    assert status.complete_status(state, None) is state


def test_missing_hot_water_channel_is_an_error_even_with_last_data():
    state, boi = status.parse_status({"R": {"V": reply(mode=True)["R"]["V"][1:]}})
    assert boi is None
    last, _ = status.parse_status(reply())
    with pytest.raises(status.IncompleteStatus):
        status.complete_status(state, last)


def test_missing_hot_water_state_is_an_error():
    state, _ = status.parse_status(reply(hot_water=False))
    with pytest.raises(status.IncompleteStatus):
        status.complete_status(state, None)


def test_missing_mode_and_schedule_are_taken_from_the_last_state():
    last, _ = status.parse_status(reply())
    state, _ = status.parse_status(reply(mode=False, schedule=False))
    status.complete_status(state, last)
    assert state["mode"] == 2
    assert state["timed_boost"] is True


def test_missing_mode_with_nothing_to_reuse_is_an_error():
    state, _ = status.parse_status(reply(mode=False))
    with pytest.raises(status.IncompleteStatus, match="mode"):
        status.complete_status(state, None)


def test_garbage_reply_is_an_error():
    state, boi = status.parse_status({"R": None})
    assert boi is None
    with pytest.raises(status.IncompleteStatus):
        status.complete_status(state, None)


def test_missing_timed_boost_is_unknown_not_a_failed_poll():
    state, _ = status.parse_status(reply(schedule=False))
    assert state["timed_boost"] is None
    assert status.complete_status(state, None) is state


def test_panel_lock_and_dst_come_from_the_timer_channel():
    state, _ = status.parse_status(reply(settings={5: 0, 28: 1, 30: 0}))
    assert state["dst"] is True
    assert state["panel_lock"] is False
    state, _ = status.parse_status(reply(settings={30: 1}))
    assert state["panel_lock"] is True
    assert state["dst"] is None


def test_missing_settings_are_unknown_not_a_failed_poll():
    state, _ = status.parse_status(reply())
    assert state["dst"] is None
    assert state["panel_lock"] is None
    assert status.complete_status(state, None) is state
