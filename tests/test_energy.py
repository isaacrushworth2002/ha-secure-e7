from zoneinfo import ZoneInfo
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


LONDON = ZoneInfo("Europe/London")
WINDOW = [{"on": "02:15", "off": "06:00"}]
NIGHT = {"saturday": WINDOW, "sunday": WINDOW}


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def placed(rows):
    out, last = {}, 0.0
    for r in rows:
        out[r["start"]] = round(r["sum"] - last, 3)
        last = r["sum"]
    return out


def night(ts, kwh, minutes, boost=0.0):
    return {
        "ts": ts,
        "total_kwh": kwh + boost,
        "boost_kwh": boost,
        "offpeak_kwh": kwh,
        "offpeak_actual_h": minutes / 60,
    }


def test_a_night_burns_from_the_window_start_for_the_actual_minutes():
    # 9.393 kWh over 193 minutes, all placed from the start of the window.
    ts = int(utc(2026, 11, 14).timestamp())
    rows = energy.daily_statistics(
        [night(ts, 9.393, 193)], "offpeak_kwh", tz=LONDON, schedule=NIGHT
    )
    assert placed(rows) == {
        utc(2026, 11, 14, 2): 2.19,
        utc(2026, 11, 14, 3): 2.92,
        utc(2026, 11, 14, 4): 2.92,
        utc(2026, 11, 14, 5): 1.363,
    }
    assert rows[-1]["sum"] == 9.393


def test_the_total_is_the_off_peak_and_boost_series_together():
    ts = int(utc(2026, 11, 14).timestamp())
    day = night(ts, 6.0, 120, boost=1.5)
    boosts = [[utc(2026, 11, 14, 17, 30).timestamp(), utc(2026, 11, 14, 18, 30).timestamp()]]
    kw = dict(tz=LONDON, schedule=NIGHT, boosts=boosts)
    total = placed(energy.daily_statistics([day], "total_kwh", **kw))
    off = placed(energy.daily_statistics([day], "offpeak_kwh", **kw))
    boost = placed(energy.daily_statistics([day], "boost_kwh", **kw))
    assert boost == {utc(2026, 11, 14, 17): 0.75, utc(2026, 11, 14, 18): 0.75}
    assert off == {utc(2026, 11, 14, 2): 2.25, utc(2026, 11, 14, 3): 3.0, utc(2026, 11, 14, 4): 0.75}
    assert total == {**off, **boost}


def test_without_a_window_or_a_recorded_boost_the_day_goes_in_its_first_hour():
    ts = int(utc(2026, 11, 13).timestamp())  # a Friday, no window in NIGHT
    day = night(ts, 3.0, 60, boost=1.0)
    for key, want in (("total_kwh", 4.0), ("offpeak_kwh", 3.0), ("boost_kwh", 1.0)):
        rows = energy.daily_statistics([day], key, tz=LONDON, schedule=NIGHT)
        assert placed(rows) == {utc(2026, 11, 13): want}


def test_a_day_with_no_actual_minutes_is_not_spread():
    ts = int(utc(2026, 11, 14).timestamp())
    rows = energy.daily_statistics([night(ts, 2.0, 0)], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    assert placed(rows) == {utc(2026, 11, 14): 2.0}


def test_minutes_beyond_the_window_are_kept_inside_it():
    ts = int(utc(2026, 11, 14).timestamp())
    rows = energy.daily_statistics([night(ts, 4.5, 300)], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    assert placed(rows) == {
        utc(2026, 11, 14, 2): 0.9,
        utc(2026, 11, 14, 3): 1.2,
        utc(2026, 11, 14, 4): 1.2,
        utc(2026, 11, 14, 5): 1.2,
    }


def test_several_windows_are_filled_in_order():
    windows = {"saturday": [{"on": "13:00", "off": "14:00"}, {"on": "02:00", "off": "03:00"}]}
    ts = int(utc(2026, 11, 14).timestamp())
    rows = energy.daily_statistics([night(ts, 6.0, 90)], "offpeak_kwh", tz=LONDON, schedule=windows)
    assert placed(rows) == {utc(2026, 11, 14, 2): 4.0, utc(2026, 11, 14, 13): 2.0}


def test_a_window_ending_at_midnight_stays_on_its_day():
    windows = {"saturday": [{"on": "23:00", "off": "24:00"}]}
    ts = int(utc(2026, 11, 14).timestamp())
    rows = energy.daily_statistics([night(ts, 3.0, 60)], "offpeak_kwh", tz=LONDON, schedule=windows)
    assert placed(rows) == {utc(2026, 11, 14, 23): 3.0}


def test_a_boost_over_midnight_is_split_between_the_two_days():
    start = utc(2026, 11, 14, 23, 30).timestamp()
    boosts = [[start, start + 3600]]
    days = [night(int(utc(2026, 11, 14).timestamp()), 0.0, 0, boost=1.0),
            night(int(utc(2026, 11, 15).timestamp()), 0.0, 0, boost=2.0)]
    rows = energy.daily_statistics(days, "boost_kwh", tz=LONDON, boosts=boosts)
    assert placed(rows) == {utc(2026, 11, 14, 23): 1.0, utc(2026, 11, 15): 2.0}
    assert [r["sum"] for r in rows] == [1.0, 3.0]


def test_an_open_boost_runs_to_now():
    start = utc(2026, 11, 14, 10).timestamp()
    day = night(int(utc(2026, 11, 14).timestamp()), 0.0, 0, boost=1.0)
    rows = energy.daily_statistics(
        [day], "boost_kwh", tz=LONDON, boosts=[[start, None]], now=start + 1800
    )
    assert placed(rows) == {utc(2026, 11, 14, 10): 1.0}


def test_clocks_going_forward_move_the_window_to_01_15_utc():
    ts = int(utc(2026, 3, 29).timestamp())  # Sunday, BST begins at 01:00 UTC
    rows = energy.daily_statistics([night(ts, 4.8, 120)], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    assert placed(rows) == {
        utc(2026, 3, 29, 1): 1.8,
        utc(2026, 3, 29, 2): 2.4,
        utc(2026, 3, 29, 3): 0.6,
    }


def test_clocks_going_back_keep_the_window_in_local_time():
    ts = int(utc(2026, 10, 25).timestamp())  # Sunday, BST ends at 01:00 UTC
    rows = energy.daily_statistics([night(ts, 4.8, 120)], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    assert placed(rows) == {
        utc(2026, 10, 25, 2): 1.8,
        utc(2026, 10, 25, 3): 2.4,
        utc(2026, 10, 25, 4): 0.6,
    }


def test_a_summer_window_in_local_time_is_an_hour_earlier_in_utc():
    ts = int(utc(2026, 7, 5).timestamp())  # Sunday
    rows = energy.daily_statistics([night(ts, 3.0, 60)], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    assert placed(rows) == {utc(2026, 7, 5, 1): 2.25, utc(2026, 7, 5, 2): 0.75}


def test_sums_stay_cumulative_and_a_late_row_moves_later_sums_only():
    sat = night(int(utc(2026, 11, 14).timestamp()), 3.0, 60)
    sun = night(int(utc(2026, 11, 15).timestamp()), 0.0, 0)
    mon = night(int(utc(2026, 11, 16).timestamp()), 3.0, 60)
    before = energy.daily_statistics([sat, sun, mon], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    sun = night(int(utc(2026, 11, 15).timestamp()), 6.0, 120)
    after = energy.daily_statistics([sat, sun, mon], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    for rows in (before, after):
        sums = [r["sum"] for r in rows]
        assert sums == sorted(sums) and [r["start"] for r in rows] == sorted(r["start"] for r in rows)
    assert before[-1]["sum"] == 6.0 and after[-1]["sum"] == 12.0
    assert [r for r in before if r["start"] < utc(2026, 11, 15)] == [
        r for r in after if r["start"] < utc(2026, 11, 15)
    ]


def test_a_growing_partial_day_is_placed_from_the_window_start_each_time():
    ts = int(utc(2026, 11, 14).timestamp())
    early = energy.daily_statistics([night(ts, 1.5, 30)], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    later = energy.daily_statistics([night(ts, 4.5, 90)], "offpeak_kwh", tz=LONDON, schedule=NIGHT)
    assert placed(early) == {utc(2026, 11, 14, 2): 1.5}
    assert placed(later) == {utc(2026, 11, 14, 2): 2.25, utc(2026, 11, 14, 3): 2.25}
