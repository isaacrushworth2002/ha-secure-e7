"""BLE transport + polling coordinator for the Secure / Horstmann E7+.

The wire protocol (four-pass AES-128 auth, 2-byte length prefix, 19-byte chunk
framing, AES-ECB JSON-RPC) is a direct port of the standalone scripts that were
proven live against the device. All BLE goes through Home Assistant's bluetooth
stack, so it works via a local adapter or an ESPHome Bluetooth proxy transparently.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CH_HOTWATER_STATE,
    CH_MODE,
    CH_TIMED_BOOST,
    CONNECT_MAX_ATTEMPTS,
    CONSUMPTION_EVERY_N_POLLS,
    DEFAULT_HW_BOI,
    DISCONNECT_TIMEOUT_SECONDS,
    DOMAIN,
    HI_GET_ALL,
    HI_GET_CONSUMPTION,
    HI_GET_SCHEDULE,
    HI_SET_SCHEDULE,
    SCHEDULE_OBJECT_ID,
    HI_WRITE,
    MAX_ENERGY_GAP_SECONDS,
    OPERATION_TIMEOUT_SECONDS,
    MODE_HOME,
    MODE_OFF,
    POLL_RETRIES,
    OT_ADVANCE,
    REPLY_TIMEOUT_SECONDS,
    RX_UUID,
    SCAN_INTERVAL_SECONDS,
    SI_BBSERVER,
    SI_DYNAMIC_TARIFF,
    SI_HOTWATER,
    SI_SCHEDULE,
    SI_THERMOSTAT,
    SLOW_READ_TIMEOUT_SECONDS,
    TX_UUID,
    WRITE_CONFIRM_DELAY_SECONDS,
    WRITE_RETRIES,
    WRITE_SETTLE_SECONDS,
)
from .backoff import ADVERT_RETRY_SECONDS, poll_interval
from .consumption import latest_row, parse_consumption, row_date
from .energy import KEEP_DAYS, gap_energy_wh, note_boost
from .energy_statistics import async_import_daily_energy
from .power import (
    element_power,
    heating_state,
    learned_watts,
    load_samples,
    typical_run_hours,
    update_samples,
)
from .resilience import PendingWrite, PollHealth, with_retries
from .protocol import (
    Reassembler,
    aes_decrypt,
    aes_encrypt,
    frame_json_encrypted,
    frame_raw,
    rpc,
    unframe,
)
from .status import IncompleteStatus, complete_status, parse_status, raw_characteristics
from .schedule import (
    DAYS,
    encode_days,
    next_change,
    parse_schedule,
    read_slots,
    splice,
    unit_timezone,
)

_LOGGER = logging.getLogger(__name__)


SecureE7ConfigEntry = ConfigEntry["SecureE7Coordinator"]


class DeviceUnreachable(UpdateFailed):
    """No adapter or proxy can see the heater, so trying again at once will not help."""


class AuthFailed(UpdateFailed):
    """The heater did not accept the key."""


class SecureE7Coordinator(DataUpdateCoordinator):
    """Keeps a (re)authenticated connection and polls device state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        mac: str,
        key: bytes,
        gmi: str,
        boost_minutes: int,
        rating_watts: int | None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self._mac = mac.upper()
        self._key = key
        self._gmi = gmi
        self.boost_minutes = boost_minutes
        self._rating_watts = rating_watts or None  # optional override
        self._client = None
        self._conn_logged = False
        self._rx = Reassembler()
        self._lock = asyncio.Lock()
        self._hw_boi: int | None = None  # hot-water channel business-object id
        self._energy_wh = 0.0  # estimated, integrated while the element is on
        self._last_energy_ts: float | None = None
        self._offpeak_credit: tuple = (None, 0.0)  # date, Wh credited to its off-peak run
        self._characteristics: dict = {}
        self.characteristic_changes: deque = deque(maxlen=200)  # for diagnostics
        self._poll_count = 0
        # Survives a reload, so failed handshakes at setup add up across retries.
        self._auth_counts: dict = hass.data.setdefault(DOMAIN, {}).setdefault("auth_failures", {})
        self._health = PollHealth(self._auth_counts.get(entry.entry_id, 0))
        self.last_success = None  # datetime of the last good poll
        self._poll_seconds = SCAN_INTERVAL_SECONDS
        self._advert_retry: float | None = None
        self._consumption: list[dict] = []  # cached daily metered history
        self._consumption_logged = False
        self._imported: tuple | None = None  # rows, programme and boosts last sent to statistics
        self._boosts: list[list] = []  # [start, end] timestamps of boosts seen in polls
        self._boost_store = Store(hass, 1, f"{DOMAIN}_boosts_{mac.replace(':', '').lower()}")
        self._boosts_loaded = False
        self._power_samples = load_samples(None)
        self._power_store = Store(hass, 1, f"{DOMAIN}_power_{mac.replace(':', '').lower()}")
        self._power_loaded = False
        self._programmes: dict[str, list] = {}  # ISO date -> that day's windows when first seen
        self._schedule: dict | None = None
        self._schedule_raw: str | None = None  # parsed weekly programme
        self._slow_task: asyncio.Task | None = None
        self._consumption_read = False  # the slow reads have worked at least once
        self._schedule_read = False
        self._pending_write = PendingWrite()
        self._cancel_confirm = None
        self._waiting = 0  # callers queued for the session lock

    # ---- connection / auth -------------------------------------------------
    async def _ensure_connected(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._mac, connectable=True
        )
        if ble_device is None:
            scanners = bluetooth.async_scanner_count(self.hass, connectable=True)
            seen_passive = (
                bluetooth.async_ble_device_from_address(
                    self.hass, self._mac, connectable=False
                )
                is not None
            )
            _LOGGER.debug(
                "secure_e7 %s not reachable: connectable_scanners=%d seen_in_passive_scan=%s",
                self._mac,
                scanners,
                seen_passive,
            )
            raise DeviceUnreachable(
                f"{self._mac} not reachable by any Bluetooth adapter/proxy in range"
            )
        self._rx.reset()
        # Resolve BleakClient at call time so it is Home Assistant's wrapped
        # client, which routes the connection through the ESPHome proxy. Using a
        # class captured at import time can fall back to the local BlueZ backend
        # (KeyError 'path') when there is no local adapter.
        from bleak import BleakClient

        if not self._conn_logged:
            _LOGGER.info(
                "secure_e7 connecting: ble_device class=%s details=%s",
                type(ble_device).__name__,
                getattr(ble_device, "details", None),
            )
            self._conn_logged = True
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._mac,
            disconnected_callback=self._on_disconnected,
            max_attempts=CONNECT_MAX_ATTEMPTS,
        )
        self._client = client  # stored first so a failure below still disconnects it
        await client.start_notify(
            TX_UUID, lambda _c, data: self._rx.feed(bytes(data))
        )
        await asyncio.sleep(0.5)
        await self._four_pass()

    def _on_disconnected(self, client) -> None:
        """The proxy dropped the link: forget the client so the next call reconnects."""
        if self._client is client:
            _LOGGER.debug("secure_e7 %s disconnected", self._mac)
            self._client = None

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            async with asyncio.timeout(DISCONNECT_TIMEOUT_SECONDS):
                await client.disconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("secure_e7 disconnect failed: %r", err)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[None]:
        """Hold the lock with an authenticated connection, for a limited time.

        Any failure drops the connection so the next call starts clean. Errors
        that are not already Home Assistant errors come out as UpdateFailed.
        """
        self._waiting += 1
        try:
            await self._lock.acquire()
        finally:
            self._waiting -= 1
        try:
            limit = asyncio.timeout(OPERATION_TIMEOUT_SECONDS)
            try:
                async with limit:
                    await self._ensure_connected()
                    yield
            except BaseException as err:
                # Includes cancellation, so a late reply never answers the next request.
                await self._disconnect()
                if isinstance(err, HomeAssistantError) or not isinstance(err, Exception):
                    raise
                if limit.expired():
                    raise UpdateFailed(
                        f"No result from {self._mac} within {OPERATION_TIMEOUT_SECONDS}s"
                    ) from err
                raise UpdateFailed(f"{type(err).__name__}: {err}") from err
        finally:
            self._lock.release()

    async def _txrx(self, packets: list[bytes], wait: float = REPLY_TIMEOUT_SECONDS) -> bytes:
        client = self._client
        if client is None:
            raise UpdateFailed("Not connected")
        self._rx.reset()
        for pkt in packets:
            await client.write_gatt_char(RX_UUID, pkt, response=True)
        try:
            await asyncio.wait_for(self._rx.event.wait(), wait)
        except asyncio.TimeoutError as err:
            raise UpdateFailed("Timed out waiting for device reply") from err
        return self._rx.assembled()

    async def _four_pass(self) -> None:
        """Authenticate, raising AuthFailed only for a well-formed wrong answer.

        A short, odd or undecryptable frame is a bad connection, not a bad key.
        """
        r_app = secrets.token_bytes(16)
        f = unframe(await self._txrx(frame_raw(bytes([1, 0, 16, 0]) + r_app)))
        if len(f) < 20 or f[0] != 2 or f[2] != 16:
            raise UpdateFailed("Malformed handshake reply")
        r_dev = f[4:20]
        f = unframe(
            await self._txrx(
                frame_raw(bytes([3, 0, 16, 0]) + aes_encrypt(self._key, r_dev))
            )
        )
        if len(f) < 20 or f[0] != 4 or f[2] != 16:
            raise UpdateFailed("Malformed handshake reply")
        if aes_decrypt(self._key, f[4:20]) != r_app:
            raise AuthFailed("The heater did not accept the key")

    async def _request_text(self, hi: int, si: int, args=None) -> str:
        """Send one encrypted request and return the decrypted reply text."""
        raw = await self._txrx(frame_json_encrypted(self._key, rpc(self._gmi, hi, si, args)))
        if len(raw) < 16 or len(raw) % 16:
            raise UpdateFailed("Malformed encrypted reply")
        return unframe(aes_decrypt(self._key, raw)).decode("utf-8", "replace")

    async def _request(self, hi: int, si: int, args=None) -> dict:
        text = await self._request_text(hi, si, args)
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise UpdateFailed("Could not parse device reply") from err

    # ---- polling -----------------------------------------------------------
    async def _async_update_data(self) -> dict:
        await self._load_power()
        try:
            state = await with_retries(
                self._poll_once, POLL_RETRIES, UpdateFailed, DeviceUnreachable
            )
        except HomeAssistantError as err:
            keep = self._health.failure(time.monotonic(), isinstance(err, AuthFailed))
            self._auth_counts[self.config_entry.entry_id] = self._health.auth_failures
            self._set_poll_interval()
            if self._health.reauth_needed:
                self._auth_counts.pop(self.config_entry.entry_id, None)
                raise ConfigEntryAuthFailed(str(err)) from err
            if keep and self.data is not None:
                _LOGGER.debug(
                    "secure_e7 %s poll %d failed, keeping last state: %s",
                    self._mac,
                    self._health.failures,
                    err,
                )
                return self.data
            if not isinstance(err, UpdateFailed):
                raise UpdateFailed(str(err)) from err
            raise
        missed = self._health.success(time.monotonic())
        self._auth_counts.pop(self.config_entry.entry_id, None)
        self.last_success = dt_util.utcnow()
        if missed:
            _LOGGER.info("secure_e7 %s back after %d failed polls", self._mac, missed)
            self._set_poll_interval()
        self._start_slow_refresh()
        return state

    async def _poll_once(self) -> dict:
        async with self._session():
            return await self._poll()

    def _set_poll_interval(self) -> None:
        seconds = poll_interval(self._health.backoff_failures, SCAN_INTERVAL_SECONDS)
        if seconds != self._poll_seconds:
            self._poll_seconds = seconds
            if seconds != SCAN_INTERVAL_SECONDS:
                _LOGGER.info(
                    "secure_e7 %s: %d failed polls in a row, next try in %ds",
                    self._mac,
                    self._health.failures,
                    seconds,
                )
            self.update_interval = timedelta(seconds=seconds)

    @callback
    def async_listen_for_heater(self) -> CALLBACK_TYPE:
        """Hear the heater advertise; returns the function that stops listening."""
        return bluetooth.async_register_callback(
            self.hass,
            self._heater_heard,
            bluetooth.BluetoothCallbackMatcher(address=self._mac, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )

    @callback
    def _heater_heard(self, *_) -> None:
        """While polls are failing, try again now instead of waiting out the backoff."""
        if not self._health.failures or self._health.reauth_needed or self._lock.locked():
            return
        now = time.monotonic()
        if self._advert_retry is not None and now - self._advert_retry < ADVERT_RETRY_SECONDS:
            return
        self._advert_retry = now
        _LOGGER.info("secure_e7 %s heard again, trying now", self._mac)
        self._health.restart_backoff()
        self._set_poll_interval()
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} {self._mac} retry"
        )

    async def _poll(self) -> dict:
        """One GetAll request; the caller holds the session."""
        obj = await self._request(HI_GET_ALL, SI_BBSERVER)
        _LOGGER.debug("secure_e7 GetAllServiceValues raw: %s", obj)
        state = self._pending_write.apply(self._parse_status(obj), time.monotonic())
        state["characteristics"] = self._note_characteristics(raw_characteristics(obj))
        self._note_boost(bool(state.get("boost_minutes_remaining")))
        state = self._derive(self._with_cached(state))
        self._integrate_energy(state)
        state["estimated_energy_kwh"] = round(self._energy_wh / 1000.0, 3)
        return state

    def _note_characteristics(self, raw: dict) -> dict:
        """Record the raw values whenever any of them change."""
        old = self._characteristics
        changed = {
            key: [old.get(key), raw.get(key)]
            for key in sorted({*old, *raw})
            if old.get(key) != raw.get(key)
        }
        if changed:
            self._characteristics = raw
            self.characteristic_changes.append(
                {"at": dt_util.now().isoformat(), "changed": changed}
            )
            _LOGGER.debug("secure_e7 characteristics changed: %s; now %s", changed, raw)
        return raw

    def _publish(self, data: dict) -> None:
        """Show new data without calling the poll good, which async_set_updated_data would.

        Entities stay unavailable through an outage until a real poll succeeds.
        """
        self.data = self._derive(data)
        self.async_update_listeners()

    def _unit_now(self, follows_dst: bool | None) -> datetime:
        """The time on the unit's own clock, which its programme runs by."""
        now = dt_util.now()
        return now.astimezone(unit_timezone(dt_util.DEFAULT_TIME_ZONE, follows_dst, now.year))

    def _derive(self, state: dict) -> dict:
        """Add whether the element is on and the estimated power that goes with it."""
        samples = self._power_samples
        now = self._unit_now(state.get("dst"))
        schedule = state.get("schedule")
        heating, basis = heating_state(
            state.get("hot_water_on"),
            state.get("timer_state"),
            state.get("mode"),
            None if schedule is None else schedule["days"].get(DAYS[now.weekday()]) or [],
            now.hour * 60 + now.minute,
        )
        programme = "boost" if basis == "hot_water" else "offpeak"
        watts, source = element_power(programme, self._rating_watts, samples)
        state["heating"] = heating
        state["heating_info"] = {
            "basis": basis,
            "thermostat_may_have_cut_out": heating is True,
        }
        state["estimated_power"] = None if heating is None else watts if heating else 0
        state["power_info"] = {
            **state["heating_info"],
            "element_watts": watts,
            "rating_source": source,
            "learned_offpeak_watts": learned_watts(samples, "offpeak"),
            "learned_boost_watts": learned_watts(samples, "boost"),
            "offpeak_days_used": len(samples["offpeak"]),
            "boost_days_used": len(samples["boost"]),
            "typical_offpeak_run_hours": typical_run_hours(samples, "offpeak"),
        }
        return state

    async def _load_power(self) -> None:
        if self._power_loaded:
            return
        try:
            saved = await self._power_store.async_load()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("secure_e7 learned power not read: %r", err)
            saved = None
        self._power_samples = load_samples(saved)
        self._power_loaded = True

    def _learn_power(self, days: list[dict]) -> None:
        if update_samples(self._power_samples, days, dt_util.DEFAULT_TIME_ZONE):
            self._power_store.async_delay_save(lambda: dict(self._power_samples), 10)

    def _with_cached(self, state: dict) -> dict:
        """Add the slowly read consumption and schedule, as last cached."""
        self._with_schedule(state)
        state["consumption"] = self._consumption
        latest = latest_row(self._consumption)
        state["consumption_date"] = (
            row_date(latest["ts"], dt_util.DEFAULT_TIME_ZONE).isoformat() if latest else None
        )
        state["energy_today_kwh"] = latest.get("total_kwh")
        state["energy_today_boost_kwh"] = latest.get("boost_kwh")
        state["energy_today_offpeak_kwh"] = latest.get("offpeak_kwh")
        state["hours_today_boost"] = latest.get("boost_actual_h")
        state["hours_today_offpeak"] = latest.get("offpeak_actual_h")
        state["hours_scheduled_boost"] = latest.get("boost_scheduled_h")
        state["hours_scheduled_offpeak"] = latest.get("offpeak_scheduled_h")
        return state

    def _start_slow_refresh(self) -> None:
        """Read consumption and the schedule after the state is published."""
        due = (
            self._poll_count % CONSUMPTION_EVERY_N_POLLS == 0
            or not (self._consumption_read and self._schedule_read)
        )
        self._poll_count += 1
        if due and (self._slow_task is None or self._slow_task.done()):
            self._slow_task = self.hass.async_create_background_task(
                self._refresh_slow(), f"{DOMAIN} {self._mac} consumption and schedule"
            )

    async def _refresh_slow(self) -> None:
        """Failures here are logged and never touch availability.

        The session is held only for the two reads, each with its own time limit,
        and they give way to a request that is waiting for the lock.
        """
        try:
            async with self._session():
                await self._read_slow()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("secure_e7 slow refresh failed: %r", err)
        if self._consumption:
            await self._import_statistics(self._consumption)
        if self.data is not None and self.last_update_success:
            self._publish(self._with_cached(dict(self.data)))

    async def _read_slow(self) -> None:
        try:
            async with asyncio.timeout(SLOW_READ_TIMEOUT_SECONDS):
                days = await self._fetch_consumption()
            self._consumption_read = True
            if days:
                self._consumption = days
                self._learn_power(days)
                if not self._consumption_logged:
                    _LOGGER.info("secure_e7 metered consumption (raw): %s", days)
                    self._consumption_logged = True
        except UpdateFailed as err:
            _LOGGER.debug("consumption fetch skipped: %s", err)
        if self._waiting:
            _LOGGER.debug("secure_e7 schedule read left for later, a request is waiting")
            return
        # Weekly programme: re-read on the same cadence so console edits
        # show up; keep the last good read on failure.
        try:
            async with asyncio.timeout(SLOW_READ_TIMEOUT_SECONDS):
                parsed = await self._fetch_schedule()
            self._schedule_read = True
            self._schedule = parsed or self._schedule
        except UpdateFailed as err:
            _LOGGER.debug("schedule read skipped: %s", err)

    async def _fetch_consumption(self) -> list[dict]:
        """Request the DynamicTariff consumption profile for the hot-water channel."""
        obj = await self._request(
            HI_GET_CONSUMPTION, SI_DYNAMIC_TARIFF, [self._hw_boi or DEFAULT_HW_BOI]
        )
        if obj.get("E") is not None:
            raise UpdateFailed(f"Consumption read refused: {obj['E']}")
        return parse_consumption(obj)

    async def _fetch_schedule(self, object_id: int | None = None) -> dict | None:
        """Read the weekly programme (object 1 unless object_id is given).

        The raw reply is logged at debug level.
        """
        boi = object_id if object_id is not None else SCHEDULE_OBJECT_ID
        text = await self._request_text(HI_GET_SCHEDULE, SI_SCHEDULE, [boi])
        self._schedule_raw = text
        _LOGGER.debug(
            "secure_e7 schedule read SI=%d HI=%d args=%s raw reply: %s",
            SI_SCHEDULE,
            HI_GET_SCHEDULE,
            [boi],
            text,
        )
        try:
            parsed = parse_schedule(json.loads(text))
        except json.JSONDecodeError:
            parsed = None
        if parsed is None:
            _LOGGER.warning("secure_e7 schedule reply not recognised; see raw reply above")
            return None
        _LOGGER.debug("secure_e7 schedule parsed: %s", parsed["days"])
        return parsed

    async def async_get_schedule(self, object_id: int | None = None) -> dict | None:
        """Re-read the weekly programme from the device and publish it.

        Diagnostic: with object_id, read that object instead and return the raw
        reply without touching the Schedule sensor.
        """
        try:
            async with self._session():
                self._schedule_raw = None
                parsed = await self._fetch_schedule(object_id)
        except UpdateFailed as err:
            raise HomeAssistantError(f"Schedule read failed: {err}") from err
        if object_id is not None:
            return {"object_id": object_id, "raw": self._schedule_raw, "parsed": parsed}
        self._schedule = parsed or self._schedule
        if self.data is not None:
            self._publish(self._with_schedule(dict(self.data)))
        return self._schedule

    async def async_set_schedule(self, changes: dict) -> dict | None:
        """Write the given days of the weekly programme; other days keep their
        device slots untouched. Reads the programme back and raises if it differs.

        Raises ValueError, before any BLE traffic, for windows the device cannot hold.
        """
        encoded = encode_days(changes)
        try:
            async with self._session():
                self._schedule_raw = None
                await self._fetch_schedule()
                base = read_slots(json.loads(self._schedule_raw or "null"))
                if base is None:
                    raise HomeAssistantError("Could not read the current schedule; nothing written")
                payload = [{"I": SCHEDULE_OBJECT_ID, "D": splice(base, encoded)}]
                expected = parse_schedule({"R": payload})
                text = await self._request_text(HI_SET_SCHEDULE, SI_SCHEDULE, payload)
                _LOGGER.debug("secure_e7 schedule write raw reply: %s", text)
                written = await self._fetch_schedule()
        except UpdateFailed as err:
            raise HomeAssistantError(f"Schedule write failed: {err}") from err
        self._schedule = written or self._schedule
        if self.data is not None:
            self._publish(self._with_schedule(dict(self.data)))
        if written != expected:
            raise HomeAssistantError(
                f"Schedule read back does not match what was written: sent {expected}, device has {written}"
            )
        return written

    def _with_schedule(self, state: dict) -> dict:
        """Add the programme and the next change worked out from it.

        This is the off-peak programme (object 1). The Timed Boost switch gates the
        boost programme, so it has no say here. The device's own "next change"
        counts the zero-length spare windows, so it is not used once a programme is read.
        """
        state["schedule"] = self._schedule
        if self._schedule is not None:
            now = self._unit_now(state.get("dst"))
            nxt = next_change(self._schedule["days"], now.weekday(), now.hour * 60 + now.minute)
            state["next_change_time"], state["next_change_state"] = nxt or (None, None)
        return state

    def _note_boost(self, active: bool) -> None:
        if self._boosts_loaded and note_boost(self._boosts, active, time.time()):
            self._save_boosts()

    def _save_boosts(self) -> None:
        self._boost_store.async_delay_save(
            lambda: {"boosts": self._boosts, "programmes": self._programmes}, 10
        )

    async def _load_boosts(self) -> None:
        if self._boosts_loaded:
            return
        try:
            saved = await self._boost_store.async_load()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("secure_e7 boost log not read: %r", err)
            saved = None
        saved = saved if isinstance(saved, dict) else {}
        boosts = saved.get("boosts")
        programmes = saved.get("programmes")
        self._boosts = [
            [b[0], b[1]]
            for b in boosts if isinstance(boosts, list) and isinstance(b, list) and len(b) == 2
            and isinstance(b[0], (int, float)) and (b[1] is None or isinstance(b[1], (int, float)))
        ] if isinstance(boosts, list) else []
        self._programmes = (
            {k: v for k, v in programmes.items() if isinstance(v, list)}
            if isinstance(programmes, dict) else {}
        )
        self._boosts_loaded = True

    def _snapshot_programmes(self, days: list[dict]) -> None:
        """Keep each day's windows as they were when the day first appeared."""
        schedule = (self._schedule or {}).get("days")
        if not schedule:
            return
        tz = dt_util.DEFAULT_TIME_ZONE
        today = dt_util.now().date()
        keep = (today - timedelta(days=KEEP_DAYS)).isoformat()
        dates = {row_date(row["ts"], tz) for row in days} | {today}
        added = False
        for day in dates:
            key = day.isoformat()
            if key >= keep and key not in self._programmes:
                self._programmes[key] = [dict(w) for w in schedule.get(DAYS[day.weekday()]) or []]
                added = True
        old = [key for key in self._programmes if key < keep]
        for key in old:
            del self._programmes[key]
        if added or old:
            self._save_boosts()

    async def _import_statistics(self, days: list[dict]) -> None:
        await self._load_boosts()
        self._snapshot_programmes(days)
        schedule = (self._schedule or {}).get("days")
        boosts = [list(b) for b in self._boosts]
        snapshots = {k: [dict(w) for w in v] for k, v in self._programmes.items()}
        window_tz = unit_timezone(
            dt_util.DEFAULT_TIME_ZONE, (self.data or {}).get("dst"), dt_util.now().year
        )
        key = (days, schedule, boosts, snapshots, window_tz)
        if key == self._imported:
            return
        try:
            await async_import_daily_energy(
                self.hass, self._mac, days, schedule, boosts, time.time(), snapshots, window_tz
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("secure_e7 energy statistics import failed: %r", err)
            return
        self._imported = key

    def _integrate_energy(self, state: dict) -> None:
        now = time.monotonic()
        if self._last_energy_ts is not None:
            wh = gap_energy_wh(
                state["heating"],
                state["estimated_power"] or 0,
                now - self._last_energy_ts,
                MAX_ENERGY_GAP_SECONDS,
            )
            if wh and state["heating_info"]["basis"] != "hot_water":
                wh = self._cap_offpeak(wh, state["estimated_power"])
            self._energy_wh += wh
        self._last_energy_ts = now

    def _cap_offpeak(self, wh: float, watts: int) -> float:
        """The thermostat cuts out part way through a window, so credit a day's
        off-peak no more than the element usually runs.
        """
        typical = typical_run_hours(self._power_samples, "offpeak")
        if typical is None:
            return wh
        today = dt_util.now().date()
        day, used = self._offpeak_credit
        if day != today:
            used = 0.0
        wh = min(wh, max(typical * watts - used, 0.0))
        self._offpeak_credit = (today, used + wh)
        return wh

    def _parse_status(self, obj: dict) -> dict:
        state, hw_boi = parse_status(obj)
        if hw_boi is not None:
            self._hw_boi = hw_boi
        try:
            return complete_status(state, self.data)
        except IncompleteStatus as err:
            raise UpdateFailed(f"Incomplete status: {err}") from err

    # ---- control -----------------------------------------------------------
    async def _write(self, si: int, boi: int, char: dict, expected: dict) -> None:
        """Send a write, show its effect at once, then refresh a few seconds later to confirm it.

        A failed write is reported to the caller and leaves the entities as they were.
        """

        async def send() -> None:
            async with self._session():
                reply = await self._request(HI_WRITE, si, [boi, char])
            if isinstance(reply, dict) and reply.get("E") is not None:
                raise HomeAssistantError(f"The heater refused the write: {reply['E']}")

        try:
            await with_retries(send, WRITE_RETRIES, UpdateFailed, DeviceUnreachable)
        except UpdateFailed as err:
            raise HomeAssistantError(f"Could not write to the heater: {err}") from err
        self._pending_write.set(expected, time.monotonic(), WRITE_SETTLE_SECONDS)
        if self.data is not None:
            self._publish({**self.data, **expected})
        if self._cancel_confirm is not None:
            self._cancel_confirm()
        self._cancel_confirm = async_call_later(
            self.hass, WRITE_CONFIRM_DELAY_SECONDS, self._confirm_write
        )

    async def _confirm_write(self, _now) -> None:
        self._cancel_confirm = None
        await self.async_request_refresh()

    async def _hw_channel(self) -> int:
        if self._hw_boi is None:
            await self.async_request_refresh()
        return self._hw_boi if self._hw_boi is not None else DEFAULT_HW_BOI

    async def async_boost(self, minutes: int) -> None:
        boi = await self._hw_channel()
        await self._write(
            SI_HOTWATER,
            boi,
            {"I": CH_HOTWATER_STATE, "V": 0, "OT": OT_ADVANCE, "D": minutes},
            {"hot_water_on": True, "boost_minutes_remaining": minutes},
        )

    async def async_cancel_boost(self) -> None:
        boi = await self._hw_channel()
        await self._write(
            SI_HOTWATER,
            boi,
            {"I": CH_HOTWATER_STATE, "V": 0, "OT": OT_ADVANCE, "D": 0},
            {"hot_water_on": False, "boost_minutes_remaining": None},
        )

    async def async_set_power(self, on: bool) -> None:
        # Master Home/Off mode lives on the Thermostat service, boi 1.
        await self._write(
            SI_THERMOSTAT,
            1,
            {"I": CH_MODE, "V": 2 if on else 0},
            {"mode": MODE_HOME if on else MODE_OFF},
        )

    async def async_set_timed_boost(self, enabled: bool) -> None:
        boi = await self._hw_channel()
        await self._write(
            SI_HOTWATER,
            boi,
            {"I": CH_TIMED_BOOST, "V": 1 if enabled else 0},
            {"timed_boost": enabled},
        )

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self._cancel_confirm is not None:
            self._cancel_confirm()
            self._cancel_confirm = None
        if self._slow_task is not None:
            self._slow_task.cancel()
        async with self._lock:
            await self._disconnect()
