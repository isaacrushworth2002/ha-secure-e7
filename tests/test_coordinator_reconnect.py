import asyncio
import types
from datetime import timedelta

import pytest

from conftest import make_coordinator


@pytest.fixture
def heater(coordinator_env, monkeypatch):
    """A coordinator with a settable clock, a failing poll and a recording Bluetooth."""
    module = coordinator_env.module
    clock = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    registered = []

    def register(hass, action, matcher, mode):
        registered.append((action, matcher, mode))
        return lambda: registered.clear()

    module.bluetooth.async_register_callback = register
    module.bluetooth.BluetoothCallbackMatcher = dict
    module.bluetooth.BluetoothScanningMode = types.SimpleNamespace(PASSIVE="passive")
    c = make_coordinator(coordinator_env)

    async def poll_once():
        raise c.error

    c._poll_once = poll_once
    c._start_slow_refresh = lambda: None
    c.error = module.DeviceUnreachable("gone")

    def fail(times=1):
        for _ in range(times):
            with pytest.raises(
                (coordinator_env.update_failed, coordinator_env.exceptions.ConfigEntryAuthFailed)
            ):
                asyncio.run(c._async_update_data())

    def hear():
        async def run():
            c._heater_heard("service_info", "advertisement")
            await asyncio.gather(*c.hass.tasks)
            c.hass.tasks.clear()

        asyncio.run(run())

    return types.SimpleNamespace(
        c=c, clock=clock, fail=fail, hear=hear, registered=registered, env=coordinator_env
    )


def seconds(c):
    return c.update_interval / timedelta(seconds=1)


def test_it_listens_for_its_own_address_without_asking_for_active_scans(heater):
    stop = heater.c.async_listen_for_heater()
    assert heater.registered == [
        (heater.c._heater_heard, {"address": "AA:BB:CC:DD:EE:FF", "connectable": True}, "passive")
    ]
    stop()
    assert heater.registered == []


def test_an_advertisement_while_all_is_well_does_nothing(heater):
    heater.hear()
    assert heater.c.refresh_requests == 0


def test_an_advertisement_while_failing_cuts_the_backoff_short(heater):
    heater.fail(6)
    assert seconds(heater.c) == 300
    heater.hear()
    assert heater.c.refresh_requests == 1
    assert seconds(heater.c) == 60
    assert heater.c._health.failures == 6


def test_the_backoff_builds_again_if_the_early_retry_fails(heater):
    heater.fail(6)
    heater.hear()
    waits = []
    for _ in range(5):
        heater.fail()
        waits.append(seconds(heater.c))
    assert waits == [60, 60, 120, 300, 300]


def test_early_retries_are_at_most_one_in_five_minutes(heater):
    heater.fail(4)
    heater.hear()
    heater.fail(4)
    heater.clock[0] += 299
    heater.hear()
    assert heater.c.refresh_requests == 1
    assert seconds(heater.c) == 300
    heater.clock[0] += 1
    heater.hear()
    assert heater.c.refresh_requests == 2
    assert seconds(heater.c) == 60


def test_nothing_happens_while_the_heater_is_busy_and_the_allowance_is_kept(heater):
    heater.fail(4)

    async def busy():
        async with heater.c._lock:
            heater.c._heater_heard()

    asyncio.run(busy())
    assert heater.c.refresh_requests == 0 and not heater.c.hass.tasks
    heater.hear()
    assert heater.c.refresh_requests == 1


def test_a_rejected_key_is_not_retried_by_advertisements(heater):
    heater.c.error = heater.env.module.AuthFailed("no")
    heater.fail(3)
    heater.hear()
    assert heater.c.refresh_requests == 0


def test_an_early_retry_does_not_bring_back_stale_state(heater):
    c = heater.c
    c.data = {"hot_water_on": True}
    c._health.success(heater.clock[0])
    for _ in range(3):
        assert asyncio.run(c._async_update_data()) == c.data
    heater.fail()
    heater.hear()
    heater.fail()


def test_a_good_poll_after_an_early_retry_returns_to_normal(heater):
    c = heater.c
    heater.fail(6)
    heater.hear()

    async def poll_once():
        return {"x": 1}

    c._poll_once = poll_once
    assert asyncio.run(c._async_update_data()) == {"x": 1}
    assert c._health.failures == 0 and c._health.backoff_failures == 0
    assert seconds(c) == 60
