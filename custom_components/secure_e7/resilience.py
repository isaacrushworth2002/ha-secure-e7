"""When to keep serving the last good state after a failed poll."""
from __future__ import annotations

MAX_STALE_FAILURES = 3
MAX_STALE_SECONDS = 300


class PollHealth:
    """Counts failed polls in a row and remembers when one last worked."""

    def __init__(self) -> None:
        self.failures = 0
        self.last_success: float | None = None

    def success(self, now: float) -> int:
        """Record a good poll and return how many failed before it."""
        missed, self.failures = self.failures, 0
        self.last_success = now
        return missed

    def failure(self, now: float) -> bool:
        """Record a failed poll; True if the last good data may still be served."""
        self.failures += 1
        return (
            self.last_success is not None
            and self.failures <= MAX_STALE_FAILURES
            and now - self.last_success <= MAX_STALE_SECONDS
        )
