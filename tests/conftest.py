"""Load pure (Home Assistant free) modules straight from the component folder.

Importing the package would pull in homeassistant, and putting the component
folder on sys.path would let its select.py shadow the stdlib module.
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "secure_e7"

PACKAGE = "secure_e7_pure"
_package = types.ModuleType(PACKAGE)
_package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = _package


def load(name: str):
    """Import a module by name, so its relative imports of other pure modules work."""
    return importlib.import_module(f"{PACKAGE}.{name}")


class FakeHass:
    """Just enough of HomeAssistant for the coordinator: a data dict and task helpers."""

    def __init__(self):
        self.data = {}
        self.tasks = []

    def async_create_background_task(self, coro, name=None):
        task = asyncio.get_event_loop().create_task(coro)
        self.tasks.append(task)
        return task

    def async_create_task(self, coro, name=None):
        return self.async_create_background_task(coro, name)


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    return module


def _ha_stubs():
    """Minimal homeassistant modules, enough to import coordinator.py offline."""

    class HomeAssistantError(Exception):
        pass

    class UpdateFailed(HomeAssistantError):
        pass

    class ConfigEntryAuthFailed(HomeAssistantError):
        pass

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, config_entry=None, name=None, update_interval=None):
            self.hass = hass
            self.config_entry = config_entry
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = True
            self.refresh_requests = 0
            self.listener_calls = 0

        def async_set_updated_data(self, data):
            self.data = data
            self.last_update_success = True
            self.async_update_listeners()

        def async_update_listeners(self):
            self.listener_calls += 1

        async def async_request_refresh(self):
            self.refresh_requests += 1

        async def async_shutdown(self):
            pass

    class ConfigEntry:
        def __class_getitem__(cls, item):
            return cls

    later_calls = []

    def async_call_later(hass, delay, action):
        later_calls.append((delay, action))
        return lambda: later_calls.remove((delay, action)) if (delay, action) in later_calls else None

    import datetime as dt

    mods = [
        _module("bleak_retry_connector", establish_connection=None),
        _module("homeassistant"),
        _module("homeassistant.components", bluetooth=_module("bluetooth")),
        _module("homeassistant.components.recorder", get_instance=None),
        _module(
            "homeassistant.components.recorder.models",
            StatisticData=dict,
            StatisticMetaData=dict,
            StatisticMeanType=types.SimpleNamespace(NONE=0),
        ),
        _module(
            "homeassistant.components.recorder.statistics",
            async_add_external_statistics=None,
            statistics_during_period=None,
        ),
        _module("homeassistant.config_entries", ConfigEntry=ConfigEntry),
        _module("homeassistant.const", UnitOfEnergy=types.SimpleNamespace(KILO_WATT_HOUR="kWh")),
        _module("homeassistant.core", HomeAssistant=object, callback=lambda f: f),
        _module(
            "homeassistant.exceptions",
            ConfigEntryAuthFailed=ConfigEntryAuthFailed,
            HomeAssistantError=HomeAssistantError,
        ),
        _module("homeassistant.helpers"),
        _module(
            "homeassistant.helpers.update_coordinator",
            DataUpdateCoordinator=DataUpdateCoordinator,
            UpdateFailed=UpdateFailed,
        ),
        _module("homeassistant.helpers.event", async_call_later=async_call_later),
        _module("homeassistant.util"),
        _module(
            "homeassistant.util.dt",
            DEFAULT_TIME_ZONE=dt.timezone.utc,
            utcnow=lambda: dt.datetime.now(dt.timezone.utc),
            now=lambda: dt.datetime.now(dt.timezone.utc),
        ),
    ]
    return {m.__name__: m for m in mods}, later_calls


@pytest.fixture
def coordinator_env(monkeypatch):
    """The real coordinator module on stubbed Home Assistant modules."""
    mods, later_calls = _ha_stubs()
    for name, module in mods.items():
        monkeypatch.setitem(sys.modules, name, module)
    for name in [n for n in sys.modules if n.startswith(PACKAGE + ".")]:
        monkeypatch.delitem(sys.modules, name)
    module = load("coordinator")
    yield types.SimpleNamespace(
        module=module,
        exceptions=mods["homeassistant.exceptions"],
        update_failed=mods["homeassistant.helpers.update_coordinator"].UpdateFailed,
        later_calls=later_calls,
    )
    for name in [n for n in sys.modules if n.startswith(PACKAGE + ".")]:
        del sys.modules[name]


def make_coordinator(env, hass=None, **kwargs):
    entry = types.SimpleNamespace(entry_id="entry1")
    return env.module.SecureE7Coordinator(
        hass or FakeHass(),
        entry,
        mac="AA:BB:CC:DD:EE:FF",
        key=bytes(16),
        gmi="1",
        boost_minutes=60,
        rating_watts=3000,
        **kwargs,
    )
