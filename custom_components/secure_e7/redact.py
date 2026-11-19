"""Hide the key, address and GMI before diagnostics leave the machine."""
from __future__ import annotations

from .const import CONF_GMI, CONF_KEY, CONF_MAC

REDACTED = "**REDACTED**"
SENSITIVE = (CONF_KEY, CONF_MAC, CONF_GMI)


def redact(value, keys=SENSITIVE):
    if isinstance(value, dict):
        return {
            k: REDACTED if k in keys else redact(v, keys) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, keys) for v in value]
    return value
