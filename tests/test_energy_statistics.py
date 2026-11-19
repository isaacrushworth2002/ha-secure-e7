import asyncio
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

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

    def during(hass, start, end, ids, period, units, types_):
        during.call = (end, ids)
        return earlier

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
    yield module, added, earlier, during
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
    module, added, earlier, during = importer
    earlier["secure_e7:aabbccddeeff_energy"] = [{"sum": 50.0}]
    asyncio.run(module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", days(2.0)))
    assert added[0][1][0]["sum"] == 52.0
    assert added[1][1][0]["sum"] == 1.0


def test_nothing_is_imported_without_rows(importer):
    module, added, _, _ = importer
    asyncio.run(module.async_import_daily_energy(None, "AA:BB:CC:DD:EE:FF", []))
    assert added == []
