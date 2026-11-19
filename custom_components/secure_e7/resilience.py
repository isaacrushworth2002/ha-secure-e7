"""When to keep serving the last good state after a failed poll."""
from __future__ import annotations

MAX_STALE_FAILURES = 3
MAX_STALE_SECONDS = 300
# Failed authentications in a row, each already on a fresh connection, before
# the key is treated as wrong. One is enough if the heater has never answered.
AUTH_FAILURES_BEFORE_REAUTH = 2


class PollHealth:
    """Counts failed polls in a row and remembers when one last worked."""

    def __init__(self) -> None:
        self.failures = 0
        self.last_success: float | None = None
        self.auth_failures = 0

    def success(self, now: float) -> int:
        """Record a good poll and return how many failed before it."""
        missed, self.failures, self.auth_failures = self.failures, 0, 0
        self.last_success = now
        return missed

    def failure(self, now: float, auth: bool = False) -> bool:
        """Record a failed poll; True if the last good data may still be served."""
        self.failures += 1
        self.auth_failures = self.auth_failures + 1 if auth else 0
        return (
            self.last_success is not None
            and self.failures <= MAX_STALE_FAILURES
            and now - self.last_success <= MAX_STALE_SECONDS
        )

    @property
    def reauth_needed(self) -> bool:
        limit = 1 if self.last_success is None else AUTH_FAILURES_BEFORE_REAUTH
        return self.auth_failures >= limit


async def with_retries(call, retries, retry_on, give_up_on=()):
    """Await call() again after a retry_on error, up to retries more times.

    Errors in give_up_on are raised at once, as retrying cannot help them.
    """
    for attempt in range(retries + 1):
        try:
            return await call()
        except give_up_on:
            raise
        except retry_on:
            if attempt == retries:
                raise
