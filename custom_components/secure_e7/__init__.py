"""Secure / Horstmann E7+ immersion heater (local BLE control).

Config-entry based so entities group under one device. Existing YAML config
(secure_e7: in configuration/packages) is imported into a config entry
automatically, so nothing needs re-entering.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    ALLOWED_BOOST_MINUTES,
    CONF_BOOST_MINUTES,
    CONF_GMI,
    CONF_KEY,
    CONF_MAC,
    CONF_RATING_WATTS,
    DEFAULT_BOOST_MINUTES,
    DEFAULT_RATING_WATTS,
    DOMAIN,
    PLATFORMS,
    SERVICE_GET_SCHEDULE,
    SERVICE_SET_SCHEDULE,
)
from .coordinator import SecureE7ConfigEntry, SecureE7Coordinator
from .migration import renamed_unique_id
from .schedule import DAYS, encode_days

PLATFORMS_T = [Platform(p) for p in PLATFORMS]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_MAC): cv.string,
                vol.Required(CONF_KEY): cv.matches_regex(r"^[0-9A-Fa-f]{32}$"),
                vol.Required(CONF_GMI): cv.string,
                vol.Optional(
                    CONF_BOOST_MINUTES, default=DEFAULT_BOOST_MINUTES
                ): vol.In(ALLOWED_BOOST_MINUTES),
                vol.Optional(
                    CONF_RATING_WATTS, default=DEFAULT_RATING_WATTS
                ): cv.positive_int,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def _coordinators(hass: HomeAssistant) -> list[SecureE7Coordinator]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def get_schedule(call: ServiceCall) -> ServiceResponse:
        """Re-read the weekly programme from every configured heater."""
        coordinators = _coordinators(hass)
        object_id = call.data.get("object_id")
        return {c._mac: await c.async_get_schedule(object_id) for c in coordinators}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        get_schedule,
        schema=vol.Schema({vol.Optional("object_id"): vol.Coerce(int)}),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def set_schedule(call: ServiceCall) -> ServiceResponse:
        """Write the weekly programme to every configured heater. Days left out are kept."""
        changes = {day: call.data[day] for day in DAYS if day in call.data}
        if not changes:
            raise ServiceValidationError("Give at least one day")
        try:
            encode_days(changes)  # fail on bad windows before touching any heater
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        coordinators = _coordinators(hass)
        return {c._mac: await c.async_set_schedule(changes) for c in coordinators}

    window = vol.Schema({vol.Required("on"): cv.string, vol.Required("off"): cv.string})
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        set_schedule,
        schema=vol.Schema({vol.Optional(day): [window] for day in DAYS}),
        supports_response=SupportsResponse.OPTIONAL,
    )

    conf = config.get(DOMAIN)
    if conf:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(conf)
            )
        )
    return True


@callback
def _migrate_entity(entity_entry: er.RegistryEntry) -> dict | None:
    new = renamed_unique_id(entity_entry.unique_id)
    return {"new_unique_id": new} if new else None


async def async_setup_entry(hass: HomeAssistant, entry: SecureE7ConfigEntry) -> bool:
    data = entry.data
    coordinator = SecureE7Coordinator(
        hass,
        entry,
        mac=data[CONF_MAC],
        key=bytes.fromhex(data[CONF_KEY]),
        gmi=str(data[CONF_GMI]),
        boost_minutes=data.get(CONF_BOOST_MINUTES, DEFAULT_BOOST_MINUTES),
        rating_watts=data.get(CONF_RATING_WATTS, DEFAULT_RATING_WATTS),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await er.async_migrate_entries(hass, entry.entry_id, _migrate_entity)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_T)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SecureE7ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS_T)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok
