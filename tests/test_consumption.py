from datetime import date, timedelta, timezone

import pytest

from conftest import load

consumption = load("consumption")
const = load("const")

DAY = 86400
T0 = 1_760_000_000 - 1_760_000_000 % DAY  # a UTC midnight


def reply(*rows):
    return {"R": [{"I": 2, "D": list(rows)}]}


def test_charge_becomes_kwh_at_230_volts():
    # 100 centi-Ah is 1 Ah, and 1 Ah at 230 V is 0.23 kWh
    row = {"T": T0, "OP": 1300.0, "BP": 100.0}
    (day,) = consumption.parse_consumption(reply(row))
    assert day["offpeak_kwh"] == pytest.approx(2.99)
    assert day["boost_kwh"] == pytest.approx(0.23)
    assert day["total_kwh"] == pytest.approx(3.22)
    assert const.CENTI_AH_TO_KWH == pytest.approx(0.0023)


def test_actual_and_scheduled_minutes_become_hours():
    row = {"T": T0, "OA": 225, "BA": 30, "OS": 225.0, "BS": 90}
    (day,) = consumption.parse_consumption(reply(row))
    assert day["offpeak_actual_h"] == 3.75
    assert day["boost_actual_h"] == 0.5
    assert day["offpeak_scheduled_h"] == 3.75
    assert day["boost_scheduled_h"] == 1.5


def test_missing_fields_are_zero_and_empty_buckets_are_dropped():
    days = consumption.parse_consumption(reply({"T": T0}, {"T": 0, "OP": 5}, {"OP": 5}))
    assert len(days) == 1
    assert days[0]["offpeak_scheduled_h"] == 0.0
    assert days[0]["total_kwh"] == 0.0


@pytest.mark.parametrize("bad", ["x", None, float("nan"), float("inf"), -5, [1]])
def test_a_bad_field_does_not_lose_the_row(bad):
    row = {"T": T0, "OP": bad, "BP": 100, "OA": bad, "OS": bad}
    (day,) = consumption.parse_consumption(reply(row))
    assert day["offpeak_kwh"] == 0.0
    assert day["boost_kwh"] == pytest.approx(0.23)
    assert day["offpeak_actual_h"] == 0.0


@pytest.mark.parametrize("obj", [None, {}, {"R": None}, {"R": "x"}, {"R": [None, {"D": 5}, {"D": [7]}]}])
def test_unrecognised_replies_give_no_rows(obj):
    assert consumption.parse_consumption(obj) == []


def test_rows_are_sorted_and_a_single_dto_is_accepted():
    days = consumption.parse_consumption({"R": {"I": 2, "D": [{"T": T0 + DAY}, {"T": T0}]}})
    assert [d["ts"] for d in days] == [T0, T0 + DAY]


BST = timezone(timedelta(hours=1))


def test_a_utc_midnight_row_is_that_utc_date_in_any_zone():
    assert consumption.row_date(T0, BST) == consumption.row_date(T0)
    assert consumption.row_date(T0, timezone(timedelta(hours=-5))) == consumption.row_date(T0)


def test_a_local_midnight_row_keeps_its_local_date():
    ts = T0 - 3600  # 00:00 BST on the day after T0's UTC date
    assert consumption.row_date(ts, BST) == consumption.row_date(T0)
    assert consumption.row_date(ts) != consumption.row_date(T0)


def test_latest_row():
    assert consumption.latest_row([]) == {}
    assert consumption.latest_row([{"ts": 1}, {"ts": 2}]) == {"ts": 2}
