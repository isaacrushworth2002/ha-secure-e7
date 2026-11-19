from datetime import datetime, timedelta, timezone

from conftest import load

energy = load("energy")

DAY = 86400
T0 = 1_700_006_400 - 1_700_006_400 % DAY  # a UTC midnight


def test_normal_gap_is_credited():
    assert energy.gap_energy_wh(True, 3000, 60, 150) == 50.0


def test_off_is_not_credited():
    assert energy.gap_energy_wh(False, 3000, 60, 150) == 0.0
    assert energy.gap_energy_wh(None, 3000, 60, 150) == 0.0


def test_outage_gap_is_not_credited():
    assert energy.gap_energy_wh(True, 3000, 3 * 3600, 150) == 0.0


def test_gap_at_the_limit_is_credited():
    assert energy.gap_energy_wh(True, 3600, 150, 150) == 150.0


def row(day, total, boost=0.0, offpeak=0.0, offset=0):
    return {"ts": T0 + day * DAY + offset, "total_kwh": total,
            "boost_kwh": boost, "offpeak_kwh": offpeak}


def test_statistics_are_keyed_on_the_row_date_and_cumulative():
    rows = energy.daily_statistics([row(0, 2.0), row(1, 6.5, offset=3600), row(2, 0.0)], "total_kwh")
    assert [r["start"] for r in rows] == [
        datetime.fromtimestamp(T0 + d * DAY, tz=timezone.utc) for d in range(3)
    ]
    assert [r["sum"] for r in rows] == [2.0, 8.5, 8.5]
    assert [r["state"] for r in rows] == [2.0, 8.5, 8.5]


def test_late_row_changes_only_its_day_and_later_sums():
    before = energy.daily_statistics([row(0, 2.0), row(1, 0.0), row(2, 1.0)], "total_kwh")
    after = energy.daily_statistics([row(0, 2.0), row(1, 6.0), row(2, 1.0)], "total_kwh")
    assert [r["sum"] for r in before] == [2.0, 2.0, 3.0]
    assert [r["sum"] for r in after] == [2.0, 8.0, 9.0]


def test_base_carries_over_from_earlier_statistics():
    rows = energy.daily_statistics([row(5, 1.5)], "total_kwh", base=100.0)
    assert rows[0]["sum"] == 101.5


def test_rows_are_unsorted_and_missing_values_are_skipped():
    days = [row(1, 1.0), row(0, 2.0), {"ts": T0 + 2 * DAY, "total_kwh": None}]
    rows = energy.daily_statistics(days, "total_kwh")
    assert [r["sum"] for r in rows] == [2.0, 3.0]


def test_each_series_uses_its_own_key():
    days = [row(0, 3.0, boost=1.0, offpeak=2.0)]
    assert energy.daily_statistics(days, "boost_kwh")[0]["sum"] == 1.0
    assert energy.daily_statistics(days, "offpeak_kwh")[0]["sum"] == 2.0


def test_a_row_stamped_at_local_midnight_is_booked_to_its_local_date():
    bst = timezone(timedelta(hours=1))
    days = [row(0, 1.0, offset=-3600), row(1, 2.0, offset=-3600)]
    rows = energy.daily_statistics(days, "total_kwh", tz=bst)
    assert [r["start"] for r in rows] == [
        datetime.fromtimestamp(T0 + d * DAY - 3600, tz=timezone.utc) for d in range(2)
    ]
    assert [r["sum"] for r in rows] == [1.0, 3.0]


def test_a_local_midnight_start_is_on_the_hour_for_a_half_hour_zone():
    ist = timezone(timedelta(hours=5, minutes=30))
    rows = energy.daily_statistics([row(0, 1.0, offset=-19800)], "total_kwh", tz=ist)
    assert rows[0]["start"].minute == 0


def test_stale_hours_hold_the_sum_of_the_row_before_them():
    def at(d):
        return datetime.fromtimestamp(T0 + d * DAY, tz=timezone.utc)

    rows = [{"start": at(0), "sum": 2.0, "state": 2.0}]
    out = energy.carry_stale_hours(rows, 0.0, [at(0), at(1), at(2)])
    assert [(r["start"], r["sum"]) for r in out] == [(at(0), 2.0), (at(1), 2.0), (at(2), 2.0)]


def test_a_stale_hour_before_the_first_row_holds_the_base():
    first = datetime.fromtimestamp(T0 + DAY, tz=timezone.utc)
    rows = [{"start": first, "sum": 5.0, "state": 5.0}]
    out = energy.carry_stale_hours(rows, 3.0, [datetime.fromtimestamp(T0, tz=timezone.utc)])
    assert [r["sum"] for r in out] == [3.0, 5.0]
