import pytest

from conftest import load

schedule = load("schedule")

OFF_SLOT = {"O": 65535, "T": 0}


def week(day_slots):
    """Build a 42-slot reply: each day padded to 6 slots with unused entries."""
    slots = []
    for day in day_slots:
        slots += day + [OFF_SLOT] * (6 - len(day))
    return {"R": slots}


def test_monday_first_on_off_pairs_become_windows():
    reply = week(
        [[{"O": 360, "T": 1}, {"O": 480, "T": 0}]]  # Mon 06:00-08:00
        + [[]] * 6
    )
    days = schedule.parse_schedule(reply)["days"]
    assert days["monday"] == [{"on": "06:00", "off": "08:00"}]
    assert days["tuesday"] == []
    assert list(days) == [
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    ]


def test_window_still_on_at_end_of_day_closes_at_midnight():
    reply = week([[]] * 6 + [[{"O": 1320, "T": 1}]])  # Sun 22:00 onwards
    days = schedule.parse_schedule(reply)["days"]
    assert days["sunday"] == [{"on": "22:00", "off": "24:00"}]


def test_unrecognised_reply_returns_none():
    assert schedule.parse_schedule({"E": {"C": 3}}) is None
    assert schedule.parse_schedule({"R": [{"O": 360, "T": 1}]}) is None  # not 42 slots
    assert schedule.parse_schedule({"R": {"V": "garbage"}}) is None


def test_slots_nested_inside_the_result_are_found():
    flat = week([[{"O": 420, "T": 1}, {"O": 450, "T": 0}]] * 7)
    reply = {"R": {"I": 2, "V": flat["R"]}}
    days = schedule.parse_schedule(reply)["days"]
    assert days["friday"] == [{"on": "07:00", "off": "07:30"}]


def test_slots_with_non_integer_times_return_none_rather_than_raising():
    reply = week([[{"O": "06:00", "T": 1}, {"O": 480, "T": 0}]] + [[]] * 6)
    assert schedule.parse_schedule(reply) is None


def test_non_dict_reply_returns_none():
    assert schedule.parse_schedule([{"O": 360, "T": 1}]) is None


def test_real_device_reply_drops_zero_length_spare_windows():
    """Captured from a real unit (object 1) on 2026-10-18; console showed 02:15-06:00."""
    day = [
        {"O": 135, "T": 1}, {"O": 360, "T": 0},
        {"O": 720, "T": 1}, {"O": 720, "T": 0},
        {"O": 720, "T": 1}, {"O": 720, "T": 0},
    ]
    reply = {"V": "1.0", "I": "0-1", "R": [{"I": 1, "D": day * 7}]}
    days = schedule.parse_schedule(reply)["days"]
    assert all(w == [{"on": "02:15", "off": "06:00"}] for w in days.values())


REAL_DAY = [
    {"O": 135, "T": 1}, {"O": 360, "T": 0},
    {"O": 720, "T": 1}, {"O": 720, "T": 0},
    {"O": 720, "T": 1}, {"O": 720, "T": 0},
]


def test_encoding_the_console_programme_reproduces_the_device_slots_exactly():
    days = {d: [{"on": "02:15", "off": "06:00"}] for d in schedule.DAYS}
    assert schedule.encode_schedule(days) == REAL_DAY * 7


def test_encode_then_parse_round_trips_several_windows():
    days = {d: [] for d in schedule.DAYS}
    days["monday"] = [{"on": "16:00", "off": "19:00"}, {"on": "02:00", "off": "05:30"}]
    days["friday"] = [
        {"on": "00:00", "off": "01:00"},
        {"on": "11:00", "off": "13:00"},
        {"on": "22:00", "off": "23:45"},
    ]
    slots = schedule.encode_schedule(days)
    assert len(slots) == 42
    for i in range(7):
        times = [s["O"] for s in slots[i * 6 : (i + 1) * 6]]
        assert times == sorted(times)
    back = schedule.parse_schedule({"R": [{"I": 1, "D": slots}]})["days"]
    assert back["monday"] == [{"on": "02:00", "off": "05:30"}, {"on": "16:00", "off": "19:00"}]
    assert back["friday"] == days["friday"]
    assert back["tuesday"] == []


@pytest.mark.parametrize(
    "windows, reason",
    [
        ([
            {"on": "01:00", "off": "02:00"}, {"on": "03:00", "off": "04:00"},
            {"on": "05:00", "off": "06:00"}, {"on": "07:00", "off": "08:00"},
        ], "at most 3"),
        ([{"on": "06:10", "off": "08:00"}], "15-minute"),
        ([{"on": "08:00", "off": "06:00"}], "before"),
        ([{"on": "08:00", "off": "08:00"}], "before"),
        ([{"on": "01:00", "off": "04:00"}, {"on": "03:00", "off": "05:00"}], "overlap"),
        ([{"on": "01:00", "off": "04:00"}, {"on": "04:00", "off": "05:00"}], "overlap"),
        ([{"on": "25:00", "off": "26:00"}], "HH:MM"),
        ([{"on": "06:00"}], "on and off"),
    ],
)
def test_invalid_windows_are_rejected_with_a_reason(windows, reason):
    days = {d: [] for d in schedule.DAYS}
    days["wednesday"] = windows
    with pytest.raises(ValueError, match=reason):
        schedule.encode_schedule(days)


def test_every_day_must_be_given():
    with pytest.raises(ValueError, match="sunday"):
        schedule.encode_schedule({d: [] for d in schedule.DAYS[:6]})


DAILY = {d: [{"on": "02:15", "off": "06:00"}] for d in schedule.DAYS}


@pytest.mark.parametrize(
    "weekday, now, expected",
    [
        (6, "09:57", ("02:15", "on")),   # Sunday morning: next is Monday 02:15 on
        (0, "01:00", ("02:15", "on")),
        (0, "02:15", ("06:00", "off")),  # a change at exactly now has happened
        (0, "03:00", ("06:00", "off")),
    ],
)
def test_next_change_is_the_next_real_edge(weekday, now, expected):
    hh, mm = map(int, now.split(":"))
    assert schedule.next_change(DAILY, weekday, hh * 60 + mm) == expected


def test_next_change_skips_empty_days_and_is_none_for_an_empty_week():
    days = {d: [] for d in schedule.DAYS}
    assert schedule.next_change(days, 2, 600) is None
    days["monday"] = [{"on": "07:00", "off": "08:00"}]
    assert schedule.next_change(days, 2, 600) == ("07:00", "on")  # Wednesday -> next Monday
    assert schedule.next_change(days, 0, 600) == ("07:00", "on")  # Monday after: a week away


def test_splice_rewrites_only_the_changed_days_and_keeps_the_rest_byte_for_byte():
    odd_tuesday = [{"O": 1320, "T": 1}] + [{"O": 65535, "T": 255}] * 5  # on to midnight
    base = REAL_DAY + odd_tuesday + REAL_DAY * 5
    changed = schedule.encode_days({"monday": [{"on": "07:00", "off": "08:00"}]})
    slots = schedule.splice(base, changed)
    assert slots[6:] == base[6:]
    assert schedule.parse_schedule({"R": [{"I": 1, "D": slots}]})["days"]["monday"] == [
        {"on": "07:00", "off": "08:00"}
    ]


def test_encode_days_validates_without_needing_the_whole_week():
    with pytest.raises(ValueError, match="15-minute"):
        schedule.encode_days({"friday": [{"on": "06:05", "off": "07:00"}]})
