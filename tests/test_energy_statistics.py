import asyncio
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from conftest import COMPONENT

DAY = 86400
T0 = 1_700_006_400 - 1_700_006_400 % DAY


def stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


@pytest.fixture
def importer(monkeypatch):
    added = []
    recorder = MagicMock()

    async def run_in_executor(func, *args):
        return func(*args)

    recorder.async_add_executor_job = run_in_executor
    earlier = {}
    later = {}

    def during(hass, start, end, ids, period, units, types_):
        return earlier if end is not None else later

    mods = {
        "homeassistant": stub("homeassistant"),
        "homeassistant.components": stub("homeassistant.components"),
        "homeassistant.components.recorder": stub(
            "homeassistant.components.recorder", get_instance=lambda hass: recorder
        ),
        "homeassistant.components.recorder.models": stub(
            "homeassistant.components.recorder.models",
            StatisticData=dict,
            StatisticMetaData=dict,
            StatisticMeanType=types.SimpleNamespace(NONE=0),
        ),
        "homeassistant.components.recorder.statistics": stub(
            "homeassistant.components.recorder.statistics",
            async_add_external_statistics=lambda hass, meta, data: added.append((meta, data)),
            statistics_during_period=during,
        ),
        "homeassistant.const": stub(
            "homeassistant.const", UnitOfEnergy=types.SimpleNamespace(KILO_WATT_HOUR="kWh")
        ),
        "homeassistant.core": stub("homeassistant.core", HomeAssistant=object),
        "homeassistant.util": stub("homeassistant.util"),
        "homeassistant.util.dt": stub(
            "homeassistant.util.dt",
            utc_from_timestamp=lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc),
            DEFAULT_TIME_ZONE=timezone.utc,
        ),
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    pkg = types.ModuleType("secure_e7_pkg")
    pkg.__path__ = [str(COMPONENT)]
    monkeypatch.setitem(sys.modules, "secure_e7_pkg", pkg)
    import importlib

    module = importlib.import_module("secure_e7_pkg.energy_statistics")
    yield module, added, earlier, later
    for name in list(sys.modules):
        if name.startswith("secure_e7_pkg"):
            del sys.modules[name]


def days(*totals):
    return [
        {"ts": T0 + i * DAY, "total_kwh": t, "boost_kwh": t / 2, "offpeak_kwh": t / 2}
        for i, t in enumerate(totals)
    ]


def test_imports_three_series_keyed_on_the_integration(importer):
    module, added, _, _ = importer
    asyncio.run(module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", days(2.0, 6.0)))
    assert [m["statistic_id"] for m, _ in added] == [
        "secure_e7:aabbccddeeff_energy",
        "secure_e7:aabbccddeeff_energy_boost",
        "secure_e7:aabbccddeeff_energy_offpeak",
    ]
    meta, rows = added[0]
    assert meta["source"] == "secure_e7" and meta["has_sum"] and meta["unit_class"] == "energy"
    assert [r["sum"] for r in rows] == [2.0, 8.0]
    assert all(r["start"].minute == 0 and r["start"].hour == 0 for r in rows)


def test_sum_continues_from_statistics_before_the_first_row(importer):
    module, added, earlier, _ = importer
    earlier["secure_e7:aabbccddeeff_energy"] = [{"sum": 50.0}]
    asyncio.run(module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", days(2.0)))
    assert added[0][1][0]["sum"] == 52.0
    assert added[1][1][0]["sum"] == 1.0


def test_nothing_is_imported_without_rows(importer):
    module, added, _, _ = importer
    asyncio.run(module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", []))
    assert added == []


def test_each_series_takes_its_base_from_before_its_own_first_row(importer):
    module, added, earlier, _ = importer
    d = days(2.0, 6.0)
    d[0]["total_kwh"] = None
    d[0]["boost_kwh"] = 1.0
    seen = []
    real = module.statistics_during_period

    def spy(hass, start, end, ids, *rest):
        seen.append((next(iter(ids)), start, end))
        return real(hass, start, end, ids, *rest)

    module.statistics_during_period = spy
    asyncio.run(module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", d))
    ends = {stat: end for stat, _, end in seen if end is not None}
    assert ends["secure_e7:aabbccddeeff_energy"] == datetime.fromtimestamp(T0 + DAY, tz=timezone.utc)
    assert ends["secure_e7:aabbccddeeff_energy_boost"] == datetime.fromtimestamp(T0, tz=timezone.utc)


def test_hours_that_the_table_no_longer_has_hold_the_running_sum(importer):
    module, added, _, later = importer
    later["secure_e7:aabbccddeeff_energy"] = [
        {"start": T0 + DAY * 1.0}, {"start": T0 + DAY * 2.0}, {"start": T0 + DAY * 3.0}
    ]
    asyncio.run(module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", days(2.0, 6.0)))
    rows = added[0][1]
    assert [r["sum"] for r in rows] == [2.0, 8.0, 8.0, 8.0]
    assert [r["start"] for r in rows][-1] == datetime.fromtimestamp(T0 + 3 * DAY, tz=timezone.utc)


def test_the_series_are_placed_in_the_hours_the_element_ran(importer):
    module, added, _, _ = importer
    row = {"ts": T0, "total_kwh": 4.0, "boost_kwh": 0.0, "offpeak_kwh": 4.0, "offpeak_actual_h": 1.0}
    window = [{"on": "02:15", "off": "06:00"}]
    names = "monday tuesday wednesday thursday friday saturday sunday".split()
    schedule = {name: window for name in names}
    asyncio.run(module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", [row], schedule))
    meta, rows = added[0]
    assert [(r["start"].hour, r["sum"]) for r in rows] == [(2, 3.0), (3, 4.0)]
    assert [r["start"].hour for r in added[2][1]] == [2, 3]


def recording(module, added):
    """Imports into a store that answers queries like the recorder does."""
    stored = {}

    def during(hass, start, end, ids, *rest):
        stat = next(iter(ids))
        return {
            stat: [
                {"start": hour.timestamp(), "sum": total}
                for hour, total in sorted(stored.get(stat, {}).items())
                if hour >= start and (end is None or hour < end)
            ]
        }

    module.statistics_during_period = during

    def run(*args, **kwargs):
        added.clear()
        asyncio.run(
            module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", *args, **kwargs)
        )
        for meta, rows in added:
            stored.setdefault(meta["statistic_id"], {}).update(
                {row["start"]: row["sum"] for row in rows}
            )
        return [total for _, total in sorted(stored["secure_e7:aabbccddeeff_energy"].items())]

    return run


def every_day(on, off):
    names = "monday tuesday wednesday thursday friday saturday sunday".split()
    return {name: [{"on": on, "off": off}] for name in names}


def offpeak_day(ts, kwh, hours):
    return {"ts": ts, "total_kwh": kwh, "boost_kwh": 0.0, "offpeak_kwh": kwh, "offpeak_actual_h": hours}


def test_a_day_moved_to_a_later_hour_is_not_counted_twice(importer):
    module, added, _, _ = importer
    run = recording(module, added)
    row = offpeak_day(T0, 6.0, 2.0)
    run([row])  # the schedule has not been read yet
    assert run([row], every_day("02:15", "06:00")) == [0.0, 2.25, 5.25, 6.0]


def test_a_late_window_survives_its_day_leaving_the_table(importer):
    module, added, _, _ = importer
    module.dt_util.DEFAULT_TIME_ZONE = ZoneInfo("Europe/London")
    run = recording(module, added)
    june = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
    table = [offpeak_day(june + i * DAY, 3.0, 0.75) for i in range(3)]
    late = every_day("23:00", "23:45")
    for first, last in ((0, 2), (1, 3)):
        sums = run(table[first:last], late, window_tz=timezone.utc)
    assert sums[-1] == 9.0
    assert sums == sorted(sums)
