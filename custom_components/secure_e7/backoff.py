"""How long to wait between polls after failures.

An ESPHome proxy stops scanning while it tries to connect, so polling a heater
it cannot reach at the normal rate keeps the proxy from ever seeing it again.
"""
from __future__ import annotations

FAILURES_BEFORE_BACKOFF = 3
BACKOFF_SECONDS = (120, 300, 600)


def poll_interval(failures: int, normal: int = 60) -> int:
    """Seconds until the next poll, given the number of failures in a row."""
    if failures < FAILURES_BEFORE_BACKOFF:
        return normal
    step = min(failures - FAILURES_BEFORE_BACKOFF, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[step]
