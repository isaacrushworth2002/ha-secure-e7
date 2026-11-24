from datetime import datetime, timezone

import pytest

from conftest import load

power = load("power")


def row(day, offpeak=(0.0, 0.0), boost=(0.0, 0.0)):
    return {
        "ts": int(datetime(2026, 11, day, tzinfo=timezone.utc).timestamp()),
        "offpeak_kwh": offpeak[0],
        "offpeak_actual_h": offpeak[1],
        "boost_kwh": boost[0],
        "boost_actual_h": boost[1],
    }


def samples_from(*rows):
    samples = power.load_samples(None)
    power.update_samples(samples, list(rows))
    return samples


def test_nothing_is_assumed_without_data():
    samples = power.load_samples(None)
    assert power.element_power("offpeak", None, samples) == (None, "none")
    assert power.typical_run_hours(samples, "offpeak") is None


def test_the_power_is_the_median_of_energy_over_run_time():
    # metered days from a 3 kW element, then one that is plainly wrong
    samples = samples_from(
        row(21, (7.144, 2.417)), row(22, (4.683, 1.55)), row(23, (4.671, 1.583)), row(24, (9.0, 1.0))
    )
    assert power.element_power("offpeak", None, samples) == (2989, "learned_offpeak")


@pytest.mark.parametrize("watts", [500, 1000, 6000])
def test_any_element_rating_is_learned(watts):
    samples = samples_from(row(1, (watts * 2 / 1000, 2.0)))
    assert power.element_power("offpeak", None, samples)[0] == watts


def test_each_programme_has_its_own_figure_and_borrows_until_it_has_one():
    only = samples_from(row(1, (6.0, 2.0)))
    assert power.element_power("boost", None, only) == (3000, "learned_offpeak")
    both = samples_from(row(1, (6.0, 2.0), (1.0, 1.0)))
    assert power.element_power("boost", None, both) == (1000, "learned_boost")


def test_short_and_empty_runs_are_not_measured():
    samples = samples_from(row(1, (0.697, 0.25), (0.014, 0.0)), row(2, (0.0, 2.0)))
    assert samples == {"offpeak": {}, "boost": {}}


def test_a_configured_value_wins():
    samples = samples_from(row(1, (6.0, 2.0)))
    assert power.element_power("offpeak", 2000, samples) == (2000, "configured")


def test_only_the_newest_days_are_kept_and_repeats_change_nothing():
    rows = [row(day, (3.0, 1.0)) for day in range(1, 21)]
    samples = power.load_samples(None)
    assert power.update_samples(samples, rows)
    assert sorted(samples["offpeak"]) == [f"2026-11-{d:02d}" for d in range(7, 21)]
    assert not power.update_samples(samples, rows)
    assert not power.update_samples(samples, [])


def test_malformed_stored_samples_are_dropped():
    saved = {"offpeak": {"a": [3.0, 1.0], "b": [0, 1], "c": "x", "d": [1]}, "boost": []}
    assert power.load_samples(saved) == {"offpeak": {"a": [3.0, 1.0]}, "boost": {}}
