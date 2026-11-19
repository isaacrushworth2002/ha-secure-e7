"""Diagnostics download for the Secure / Horstmann E7+."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from .coordinator import SecureE7ConfigEntry
from .redact import redact


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SecureE7ConfigEntry
) -> dict:
    coordinator = entry.runtime_data
    return {
        "entry": redact(dict(entry.data)),
        "last_update_success": coordinator.last_update_success,
        "last_success": coordinator.last_success,
        "failed_polls_in_a_row": coordinator._health.failures,
        "poll_interval_seconds": coordinator._poll_seconds,
        "data": redact(coordinator.data),
    }
