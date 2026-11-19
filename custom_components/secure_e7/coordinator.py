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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CENTI_AH_TO_KWH,
    CH_HOTWATER_STATE,
    CH_MODE,
    CH_SCHEDULE_ENABLE,
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
    K_BOOST_ACTUAL_MIN,
    K_BOOST_CHARGE,
    K_DTO_DAYS,
    K_DTO_ID,
    K_OFFPEAK_ACTUAL_MIN,
    K_OFFPEAK_CHARGE,
    K_TIME,
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
    TX_UUID,
    WRITE_RETRIES,
)
from .backoff import poll_interval
from .energy import gap_energy_wh
from .energy_statistics import async_import_daily_energy
from .resilience import PollHealth, with_retries
from .protocol import (
    Reassembler,
    aes_decrypt,
    aes_encrypt,
    frame_json_encrypted,
    frame_raw,
    rpc,
    unframe,
)
from .status import IncompleteStatus, complete_status, parse_status
from .schedule import encode_days, next_change, parse_schedule, read_slots, splice

_LOGGER = logging.getLogger(__name__)


def parse_consumption(obj: dict) -> list[dict]:
    """DynamicTariff getConsumptionState reply -> list of per-day dicts.

    R = [ {"I": channelId, "D": [ {"T":unix,"OP":f,"BP":f,"OA":f,"BA":f,...} ] } ]
    kWh = charge_centi_ah * 230 / 100000 (mirrors ConsumptionProfile.java).
    """
    result = obj.get("R")
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        return []
    days: list[dict] = []
    for dto in result:
        if not isinstance(dto, dict):
            continue
        for row in dto.get(K_DTO_DAYS, []) or []:
            t = int(row.get(K_TIME, 0) or 0)
            if t == 0:
                continue
            bp = float(row.get(K_BOOST_CHARGE, 0) or 0)
            op = float(row.get(K_OFFPEAK_CHARGE, 0) or 0)
            days.append(
                {
                    "channel_id": dto.get(K_DTO_ID),
                    "ts": t,
                    "boost_kwh": round(bp * CENTI_AH_TO_KWH, 3),
                    "offpeak_kwh": round(op * CENTI_AH_TO_KWH, 3),
                    "total_kwh": round((bp + op) * CENTI_AH_TO_KWH, 3),
                    "boost_charge_centi_ah": bp,
                    "offpeak_charge_centi_ah": op,
                    "boost_actual_h": round(float(row.get(K_BOOST_ACTUAL_MIN, 0) or 0) / 60.0, 3),
                    "offpeak_actual_h": round(float(row.get(K_OFFPEAK_ACTUAL_MIN, 0) or 0) / 60.0, 3),
                }
            )
    days.sort(key=lambda d: d["ts"])
    return days


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
        rating_watts: int,
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
        self._rating_watts = rating_watts
        self._client = None
        self._conn_logged = False
        self._rx = Reassembler()
        self._lock = asyncio.Lock()
        self._hw_boi: int | None = None  # hot-water channel business-object id
        self._energy_wh = 0.0  # estimated, integrated while the element is on
        self._last_energy_ts: float | None = None
        self._poll_count = 0
        self._health = PollHealth()
        self.last_success = None  # datetime of the last good poll
        self._poll_seconds = SCAN_INTERVAL_SECONDS
        self._consumption: list[dict] = []  # cached daily metered history
        self._consumption_logged = False
        self._imported: list[dict] | None = None  # rows last sent to statistics
        self._schedule: dict | None = None
        self._schedule_raw: str | None = None  # parsed weekly programme
        self._slow_task: asyncio.Task | None = None

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
            _LOGGER.warning(
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
        if not await self._four_pass():
            await self._disconnect()
            raise AuthFailed("Authentication (four-pass) failed")

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
        async with self._lock:
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

    async def _four_pass(self) -> bool:
        r_app = secrets.token_bytes(16)
        f = unframe(await self._txrx(frame_raw(bytes([1, 0, 16, 0]) + r_app)))
        if len(f) < 4 or f[0] != 2:
            return False
        r_dev = f[4 : 4 + f[2]]
        f = unframe(
            await self._txrx(
                frame_raw(bytes([3, 0, 16, 0]) + aes_encrypt(self._key, r_dev))
            )
        )
        if len(f) < 4 or f[0] != 4:
            return False
        return aes_decrypt(self._key, f[4 : 4 + f[2]])[:16] == r_app

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
        try:
            state = await with_retries(
                self._poll_once, POLL_RETRIES, UpdateFailed, DeviceUnreachable
            )
        except UpdateFailed as err:
            keep = self._health.failure(time.monotonic(), isinstance(err, AuthFailed))
            self._set_poll_interval()
            if self._health.reauth_needed:
                raise ConfigEntryAuthFailed(str(err)) from err
            if keep and self.data is not None:
                _LOGGER.debug(
                    "secure_e7 %s poll %d failed, keeping last state: %s",
                    self._mac,
                    self._health.failures,
                    err,
                )
                return self.data
            raise
        missed = self._health.success(time.monotonic())
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
        seconds = poll_interval(self._health.failures, SCAN_INTERVAL_SECONDS)
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

    async def _poll(self) -> dict:
        """One GetAll request; the caller holds the session."""
        obj = await self._request(HI_GET_ALL, SI_BBSERVER)
        _LOGGER.debug("secure_e7 GetAllServiceValues raw: %s", obj)
        state = self._parse_status(obj)
        self._integrate_energy(state.get("hot_water_on"))
        state["estimated_power"] = (
            self._rating_watts if state.get("hot_water_on") else 0
        )
        state["estimated_energy_kwh"] = round(self._energy_wh / 1000.0, 3)
        return self._with_cached(state)

    def _with_cached(self, state: dict) -> dict:
        """Add the slowly read consumption and schedule, as last cached."""
        self._with_schedule(state)
        state["consumption"] = self._consumption
        latest = self._consumption[-1] if self._consumption else {}
        state["energy_today_kwh"] = latest.get("total_kwh")
        state["energy_today_boost_kwh"] = latest.get("boost_kwh")
        state["energy_today_offpeak_kwh"] = latest.get("offpeak_kwh")
        state["hours_today_boost"] = latest.get("boost_actual_h")
        state["hours_today_offpeak"] = latest.get("offpeak_actual_h")
        return state

    def _start_slow_refresh(self) -> None:
        """Read consumption and the schedule after the state is published."""
        due = self._poll_count % CONSUMPTION_EVERY_N_POLLS == 0
        self._poll_count += 1
        if due and (self._slow_task is None or self._slow_task.done()):
            self._slow_task = self.hass.async_create_background_task(
                self._refresh_slow(), f"{DOMAIN} {self._mac} consumption and schedule"
            )

    async def _refresh_slow(self) -> None:
        """Failures here are logged and never touch availability."""
        try:
            async with self._session():
                try:
                    days = await self._fetch_consumption()
                    if days:
                        self._consumption = days
                        await self._import_statistics(days)
                        if not self._consumption_logged:
                            _LOGGER.info("secure_e7 metered consumption (raw): %s", days)
                            self._consumption_logged = True
                except UpdateFailed as err:
                    _LOGGER.debug("consumption fetch skipped: %s", err)
                # Weekly programme: re-read on the same cadence so console edits
                # show up; keep the last good read on failure.
                try:
                    self._schedule = await self._fetch_schedule() or self._schedule
                except UpdateFailed as err:
                    _LOGGER.debug("schedule read skipped: %s", err)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("secure_e7 slow refresh failed: %r", err)
            return
        if self.data is not None and self.last_update_success:
            self.async_set_updated_data(self._with_cached(dict(self.data)))

    async def _fetch_consumption(self) -> list[dict]:
        """Request the DynamicTariff consumption profile for the hot-water channel."""
        obj = await self._request(
            HI_GET_CONSUMPTION, SI_DYNAMIC_TARIFF, [self._hw_boi or DEFAULT_HW_BOI]
        )
        return [] if obj.get("E") is not None else parse_consumption(obj)

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
            self.async_set_updated_data(self._with_schedule(dict(self.data)))
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
            self.async_set_updated_data(self._with_schedule(dict(self.data)))
        if written != expected:
            raise HomeAssistantError(
                f"Schedule read back does not match what was written: sent {expected}, device has {written}"
            )
        return written

    def _with_schedule(self, state: dict) -> dict:
        """Add the programme and the next change worked out from it.

        The device's own "next change" counts the zero-length spare windows
        (and object 2 is unused), so it is not used once a programme is read.
        """
        state["schedule"] = self._schedule
        if self._schedule is not None:
            nxt = None
            if state.get("schedule_enabled") is not False:
                now = dt_util.now()
                nxt = next_change(self._schedule["days"], now.weekday(), now.hour * 60 + now.minute)
            state["next_change_time"], state["next_change_state"] = nxt or (None, None)
        return state

    async def _import_statistics(self, days: list[dict]) -> None:
        if days == self._imported:
            return
        try:
            await async_import_daily_energy(self.hass, self._mac, days)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("secure_e7 energy statistics import failed: %r", err)
            return
        self._imported = days

    def _integrate_energy(self, on: bool | None) -> None:
        now = time.monotonic()
        if self._last_energy_ts is not None:
            self._energy_wh += gap_energy_wh(
                on, self._rating_watts, now - self._last_energy_ts, MAX_ENERGY_GAP_SECONDS
            )
        self._last_energy_ts = now

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
        """Send a write, show its effect at once, then refresh to confirm it.

        A failed write is reported to the caller and leaves the entities as they were.
        """

        async def send() -> None:
            async with self._session():
                await self._request(HI_WRITE, si, [boi, char])

        try:
            await with_retries(send, WRITE_RETRIES, UpdateFailed, DeviceUnreachable)
        except UpdateFailed as err:
            raise HomeAssistantError(f"Could not write to the heater: {err}") from err
        if self.data is not None:
            self.async_set_updated_data({**self.data, **expected})
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

    async def async_set_schedule_enabled(self, enabled: bool) -> None:
        boi = await self._hw_channel()
        await self._write(
            SI_HOTWATER,
            boi,
            {"I": CH_SCHEDULE_ENABLE, "V": 1 if enabled else 0},
            {"schedule_enabled": enabled},
        )

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self._slow_task is not None:
            self._slow_task.cancel()
        async with self._lock:
            await self._disconnect()
