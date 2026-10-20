"""Config flow for Secure / Horstmann E7+.

Paths to add a heater:
  * Bluetooth discovery, or manual entry of the MAC, then
  * "Commission": the integration pulls the key automatically once the unit is
    held in commissioning mode (hold Menu ~8s), or
  * "Manual": paste a key you already have.
  * Import: existing YAML config is migrated to a config entry.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers import config_validation as cv

from .commission import CommissionError, async_commission, mac_to_gmi
from .const import CONF_GMI, CONF_KEY, CONF_MAC, DOMAIN

_LOGGER = logging.getLogger(__name__)

TITLE = "Immersion Heater"
_COMMISSION_ERRORS = ("not_reachable", "not_commissioning", "no_key", "ble_error", "timeout")
_KEY_CHARS = set("0123456789abcdef")


class SecureE7ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None

    # --- entry points ---------------------------------------------------
    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak):
        self._address = discovery_info.address
        await self.async_set_unique_id(self._address.upper())
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": TITLE}
        return await self.async_step_method()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._address = str(user_input[CONF_MAC]).upper()
            await self.async_set_unique_id(self._address)
            self._abort_if_unique_id_configured()
            return await self.async_step_method()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_MAC): cv.string}),
        )

    async def async_step_import(self, data: dict):
        await self.async_set_unique_id(str(data[CONF_MAC]).upper())
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=TITLE, data=data)

    # --- choose how to get the key -------------------------------------
    async def async_step_method(self, user_input=None):
        return self.async_show_menu(
            step_id="method", menu_options=["commission", "manual"]
        )

    async def async_step_commission(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                key_hex = await async_commission(self.hass, self._address)
            except CommissionError as err:
                code = str(err)
                errors["base"] = code if code in _COMMISSION_ERRORS else "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("secure_e7 commissioning failed")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=TITLE, data=self._entry_data(key_hex)
                )
        return self.async_show_form(
            step_id="commission",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"address": self._address or ""},
        )

    async def async_step_manual(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            key = str(user_input[CONF_KEY]).strip().lower()
            if len(key) == 32 and set(key) <= _KEY_CHARS:
                return self.async_create_entry(
                    title=TITLE, data=self._entry_data(key)
                )
            errors["base"] = "bad_key"
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_KEY): cv.string}),
            errors=errors,
        )

    def _entry_data(self, key_hex: str) -> dict:
        assert self._address is not None
        return {
            CONF_MAC: self._address,
            CONF_KEY: key_hex,
            CONF_GMI: mac_to_gmi(self._address),
        }
