import asyncio
from datetime import datetime, time, timezone

import pytest

from conftest import make_coordinator


def prepare(env, **kwargs):
    c = make_coordinator(env, **kwargs)
    c.events = []

    async def connected():
        c.events.append("connect")

    async def disconnect():
        c.events.append("disconnect")

    c._ensure_connected = connected
    c._disconnect = disconnect
    return c


def test_the_statistics_import_runs_after_the_lock_is_released(coordinator_env):
    c = prepare(coordinator_env)
    seen = {}

    async def consumption():
        seen["during_read"] = c._lock.locked()
        return [{"ts": 1, "total_kwh": 1.0}]

    async def schedule():
        return {"days": {}}

    async def import_statistics(days):
        seen["during_import"] = c._lock.locked()

    c._fetch_consumption, c._fetch_schedule = consumption, schedule
    c._import_statistics = import_statistics
    asyncio.run(c._refresh_slow())
    assert seen == {"during_read": True, "during_import": False}
    assert c._consumption_read and c._schedule_read


def test_a_slow_read_that_hangs_is_cut_off_and_frees_the_lock(coordinator_env):
    coordinator_env.module.SLOW_READ_TIMEOUT_SECONDS = 0.05
    c = prepare(coordinator_env)

    async def hang():
        await asyncio.sleep(10)

    c._fetch_consumption = hang
    asyncio.run(asyncio.wait_for(c._refresh_slow(), 2))
    assert not c._lock.locked()
    assert "disconnect" in c.events
    assert not c._consumption_read


def test_a_waiting_request_is_served_before_the_schedule_is_read(coordinator_env):
    c = prepare(coordinator_env)
    order = []

    async def consumption():
        await asyncio.sleep(0)
        return []

    async def schedule():
        order.append("schedule")
        return None

    async def write():
        async with c._session():
            order.append("write")

    c._fetch_consumption, c._fetch_schedule = consumption, schedule

    async def run():
        waiter = asyncio.ensure_future(write())
        await c._refresh_slow()
        await waiter

    asyncio.run(run())
    assert order == ["write"]
    assert not c._schedule_read


def test_slow_reads_are_retried_every_poll_until_both_have_worked(coordinator_env):
    c = prepare(coordinator_env)
    started = []

    async def refresh():
        started.append(1)

    c._refresh_slow = refresh

    async def run():
        c._poll_count = 1
        c._start_slow_refresh()
        await c._slow_task
        c._consumption_read = True
        c._start_slow_refresh()
        await c._slow_task
        c._schedule_read = True
        c._start_slow_refresh()
        c._start_slow_refresh()

    asyncio.run(run())
    assert len(started) == 2


def test_the_session_times_out_with_update_failed_and_drops_the_connection(coordinator_env):
    coordinator_env.module.OPERATION_TIMEOUT_SECONDS = 0.05
    c = prepare(coordinator_env)

    async def run():
        async with c._session():
            await asyncio.sleep(10)

    with pytest.raises(coordinator_env.update_failed, match="No result"):
        asyncio.run(run())
    assert c.events == ["connect", "disconnect"]
    assert not c._lock.locked()


def test_an_unexpected_error_in_the_session_becomes_update_failed(coordinator_env):
    c = prepare(coordinator_env)

    async def run():
        async with c._session():
            raise KeyError("x")

    with pytest.raises(coordinator_env.update_failed, match="KeyError"):
        asyncio.run(run())
    assert "disconnect" in c.events and not c._lock.locked()


def test_home_assistant_errors_and_cancellation_pass_through_after_disconnecting(coordinator_env):
    c = prepare(coordinator_env)

    async def fail():
        async with c._session():
            raise coordinator_env.exceptions.HomeAssistantError("mine")

    with pytest.raises(coordinator_env.exceptions.HomeAssistantError, match="mine"):
        asyncio.run(fail())

    async def cancelled():
        async with c._session():
            await asyncio.sleep(10)

    async def run():
        task = asyncio.ensure_future(cancelled())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert c.events.count("disconnect") == 2 and not c._lock.locked()


def write_with_reply(env, reply):
    c = prepare(env)
    calls = []

    async def request(hi, si, args=None):
        calls.append(1)
        return reply

    c._request = request
    asyncio.run(c._write(1, 1, {"I": 1}, {"mode": "home"}))
    return c, calls


def test_a_write_the_device_refuses_raises_and_changes_nothing(coordinator_env):
    with pytest.raises(coordinator_env.exceptions.HomeAssistantError, match="refused"):
        write_with_reply(coordinator_env, {"E": {"C": 5}})


def test_a_refused_write_is_not_retried_or_shown(coordinator_env):
    c = prepare(coordinator_env)
    calls = []

    async def request(hi, si, args=None):
        calls.append(1)
        return {"E": 1}

    c._request = request
    c.data = {"mode": "off"}
    with pytest.raises(coordinator_env.exceptions.HomeAssistantError):
        asyncio.run(c._write(1, 1, {"I": 1}, {"mode": "home"}))
    assert len(calls) == 1 and c.data == {"mode": "off"}


def test_an_accepted_write_is_shown(coordinator_env):
    c = prepare(coordinator_env)
    c.data = {"mode": "off"}

    async def request(hi, si, args=None):
        return {"R": []}

    c._request = request
    asyncio.run(c._write(1, 1, {"I": 1}, {"mode": "home"}))
    assert c.data == {"mode": "home"}


def test_publishing_does_not_mark_a_failed_poll_good(coordinator_env):
    c = prepare(coordinator_env)
    c.last_update_success = False
    c.data = {"schedule": None}
    c._schedule = {"days": {}}
    c._publish(c._with_schedule(dict(c.data)))
    assert c.last_update_success is False
    assert c.data["schedule"] == {"days": {}}
    assert c.listener_calls == 1


def test_a_write_during_an_outage_keeps_entities_unavailable(coordinator_env):
    c = prepare(coordinator_env)
    c.last_update_success = False
    c.data = {"mode": "off"}

    async def request(hi, si, args=None):
        return {}

    c._request = request
    asyncio.run(c._write(1, 1, {"I": 1}, {"mode": "home"}))
    assert c.last_update_success is False and c.data == {"mode": "home"}


def test_a_write_schedules_a_delayed_refresh_instead_of_an_immediate_one(coordinator_env):
    c = prepare(coordinator_env)
    c.data = {"mode": "off"}

    async def request(hi, si, args=None):
        return {}

    c._request = request
    asyncio.run(c._write(1, 1, {"I": 1}, {"mode": "home"}))
    assert c.refresh_requests == 0
    assert [d for d, _ in coordinator_env.later_calls] == [5]
    asyncio.run(c._confirm_write(None))
    assert c.refresh_requests == 1


def test_a_second_write_replaces_the_pending_refresh(coordinator_env):
    c = prepare(coordinator_env)

    async def request(hi, si, args=None):
        return {}

    c._request = request

    async def run():
        await c._write(1, 1, {"I": 1}, {"mode": "home"})
        await c._write(1, 1, {"I": 1}, {"mode": "off"})

    asyncio.run(run())
    assert len(coordinator_env.later_calls) == 1


def test_shutdown_cancels_the_pending_refresh(coordinator_env):
    c = prepare(coordinator_env)

    async def request(hi, si, args=None):
        return {}

    c._request = request

    async def run():
        await c._write(1, 1, {"I": 1}, {"mode": "home"})
        await c.async_shutdown()

    asyncio.run(run())
    assert coordinator_env.later_calls == []


def test_polls_record_a_boost_and_the_import_gets_it_with_the_programme(coordinator_env):
    c = prepare(coordinator_env)
    sent = []

    async def importer(hass, mac, days, schedule, boosts, now, snapshots):
        sent.append((days, schedule, [list(b) for b in boosts]))

    coordinator_env.module.async_import_daily_energy = importer
    c._schedule = {"days": {"monday": []}}
    asyncio.run(c._load_boosts())
    c._note_boost(True)
    c._note_boost(False)
    asyncio.run(c._import_statistics([{"ts": 1}]))
    asyncio.run(c._import_statistics([{"ts": 1}]))
    assert len(sent) == 1
    days, schedule, boosts = sent[0]
    assert schedule == {"monday": []} and len(boosts) == 1 and boosts[0][1] >= boosts[0][0]


def test_a_day_keeps_the_windows_it_first_had_when_the_programme_is_edited(coordinator_env):
    c = prepare(coordinator_env)
    sent = []

    async def importer(hass, mac, days, schedule, boosts, now, snapshots):
        sent.append((schedule, snapshots))

    coordinator_env.module.async_import_daily_energy = importer
    old = [{"on": "02:00", "off": "06:00"}]
    new = [{"on": "10:00", "off": "11:00"}]
    today = datetime.now(timezone.utc).date()
    days = [{"ts": int(datetime.combine(today, time(), timezone.utc).timestamp())}]
    day = today.isoformat()
    weekday = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[today.weekday()]
    c._schedule = {"days": {weekday: old}}
    asyncio.run(c._import_statistics(days))
    c._schedule = {"days": {weekday: new}}
    asyncio.run(c._import_statistics(days))
    assert sent[0][1][day] == old and sent[1][1][day] == old
    assert sent[1][0] == {weekday: new}
    assert c._boost_store.saved["programmes"][day] == old


def test_snapshots_and_boosts_come_back_after_a_restart_and_junk_is_ignored(coordinator_env):
    c = prepare(coordinator_env)
    c._boost_store.saved = {
        "boosts": [[1.0, 2.0], [3.0, None], "x", [1, 2, 3], ["a", 1]],
        "programmes": {"2026-11-14": [{"on": "02:00", "off": "03:00"}], "bad": 5},
    }
    asyncio.run(c._load_boosts())
    assert c._boosts == [[1.0, 2.0], [3.0, None]]
    assert c._programmes == {"2026-11-14": [{"on": "02:00", "off": "03:00"}]}
    c2 = prepare(coordinator_env)
    c2._boost_store.saved = ["not", "a", "dict"]
    asyncio.run(c2._load_boosts())
    assert c2._boosts == [] and c2._programmes == {}
