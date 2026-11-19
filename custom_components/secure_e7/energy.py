"""Energy bookkeeping that needs no Home Assistant, so it can be unit tested."""
from __future__ import annotations


def gap_energy_wh(on: bool | None, watts: float, elapsed: float, max_gap: float) -> float:
    """Wh to credit for the time since the last successful poll.

    A longer gap means polls failed, so we do not know the heater was on.
    """
    if not on or elapsed < 0 or elapsed > max_gap:
        return 0.0
    return watts * elapsed / 3600.0

