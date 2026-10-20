"""Commissioning: pull a device's BLE key automatically.

When the E7+ is put into commissioning mode (hold Menu ~8 s on the unit), it
talks in cleartext and will hand out its BLE key. We read the key, claim the
device as owner (which re-keys it, so the vendor app loses control), then read
the final key that is used for normal four-pass authenticated control.

This is a port of the proven standalone commissioning script. It runs over
Home Assistant's Bluetooth stack, so it works through an ESPHome proxy.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging

from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import (
    CONNECT_MAX_ATTEMPTS,
    DISCONNECT_TIMEOUT_SECONDS,
    OPERATION_TIMEOUT_SECONDS,
    REPLY_TIMEOUT_SECONDS,
    RX_UUID,
    TX_UUID,
)
from .protocol import Reassembler, frame_json, rpc, unframe

_LOGGER = logging.getLogger(__name__)

DEFAULT_OWNER_EMAIL = "homeassistant@local"
DEFAULT_SYSTEM_NAME = "Immersion"

# commissioning JSON-RPC ids
_HI_GET_BLE_KEY = 50
_SI_HAN_MGMT = 11
_HI_SET_OWNER = 7
_SI_BBSERVER = 151


class CommissionError(Exception):
    """Raised when the key could not be read. The message is an error code."""


def mac_to_gmi(mac: str) -> str:
    """Reversed-byte MAC, as a decimal string (the GMI the device expects)."""
    raw = bytes.fromhex(mac.replace(":", ""))
    return str(int.from_bytes(raw[::-1], "big"))


def _key(reply: bytes | None) -> bytes | None:
    """The 16-byte key in a GetBLEKey reply, or None."""
    if not reply:
        return None
    try:
        key_b64 = json.loads(unframe(reply).decode("utf-8", "replace")).get("R")
        key = base64.b64decode(key_b64) if isinstance(key_b64, str) else b""
    except (json.JSONDecodeError, AttributeError, binascii.Error, ValueError):
        return None
    return key if len(key) == 16 else None


async def async_commission(
    hass: HomeAssistant,
    address: str,
    owner_email: str = DEFAULT_OWNER_EMAIL,
    system_name: str = DEFAULT_SYSTEM_NAME,
) -> str:
    """Return the device's BLE key as a 32-char hex string. Raises CommissionError."""
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), connectable=True)
    if ble_device is None:
        raise CommissionError("not_reachable")

    from bleak import BleakClient

    try:
        async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
            client = await establish_connection(
                BleakClient, ble_device, address, max_attempts=CONNECT_MAX_ATTEMPTS
            )
    except (BleakError, TimeoutError) as err:
        raise CommissionError("not_reachable") from err

    # The time limit covers the requests only, so a key that has arrived is
    # never lost to a slow disconnect.
    try:
        async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
            return await _claim(client, mac_to_gmi(address), owner_email, system_name)
    except TimeoutError as err:
        raise CommissionError("timeout") from err
    except BleakError as err:
        raise CommissionError("ble_error") from err
    finally:
        try:
            async with asyncio.timeout(DISCONNECT_TIMEOUT_SECONDS):
                await client.disconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("secure_e7 commissioning disconnect failed: %r", err)


async def _claim(client, gmi: str, owner_email: str, system_name: str) -> str:
    rx = Reassembler()

    async def request(hi: int, si: int, args=None) -> bytes | None:
        """Send a cleartext request. Return the reply, or None on timeout."""
        rx.reset()
        for pkt in frame_json(rpc(gmi, hi, si, args)):
            await client.write_gatt_char(RX_UUID, pkt, response=True)
        try:
            await asyncio.wait_for(rx.event.wait(), REPLY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return None
        return rx.assembled()

    await client.start_notify(TX_UUID, lambda _c, d: rx.feed(bytes(d)))
    await asyncio.sleep(1)
    # No answer at all means the unit is not listening in commissioning mode,
    # so stop rather than claim it.
    reply = await request(_HI_GET_BLE_KEY, _SI_HAN_MGMT)
    if reply is None:
        raise CommissionError("not_commissioning")
    if _key(reply) is None:
        _LOGGER.debug("secure_e7 first GetBLEKey reply has no key: %r", unframe(reply))
    # Claim ownership (re-keys the device; vendor app loses control)
    owner = [{"SN": system_name, "MN": None, "UEA": owner_email}]
    await request(_HI_SET_OWNER, _SI_BBSERVER, owner)
    await asyncio.sleep(1)
    # A late SetOwner reply can land in the first read, so try twice.
    for _ in range(2):
        key = _key(await request(_HI_GET_BLE_KEY, _SI_HAN_MGMT))
        if key is not None:
            return key.hex()
    raise CommissionError("no_key")
