"""When to keep serving the last good state after a failed poll."""
from __future__ import annotations

MAX_STALE_FAILURES = 3
MAX_STALE_SECONDS = 300
# Handshakes in a row that were well formed but wrong, each already on a fresh
# connection, before the key is treated as wrong.
AUTH_FAILURES_BEFORE_REAUTH = 3


class PollHealth:
    """Counts failed polls in a row and remembers when one last worked."""

    def __init__(self, auth_failures: int = 0) -> None:
        self.failures = 0
        self.last_success: float | None = None
        self.auth_failures = auth_failures

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
        return self.auth_failures >= AUTH_FAILURES_BEFORE_REAUTH


class PendingWrite:
    """What was last written, shown over polls that may still read the old state."""

    def __init__(self) -> None:
        self.expected: dict = {}
        self.until = 0.0

    def set(self, expected: dict, now: float, settle: float) -> None:
        self.expected = {**self.expected, **expected} if now < self.until else dict(expected)
        self.until = now + settle

    def apply(self, state: dict, now: float) -> dict:
        return {**state, **self.expected} if now < self.until else state


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
