"""Changes to stored data between releases, kept free of Home Assistant."""
from __future__ import annotations

from collections.abc import Callable

# Old key -> new key. The entity registry keeps the entity id, so automations
# and dashboards that use it carry on working.
RENAMED_KEYS = {"follow_schedule": "timed_boost"}

# Earlier releases filled this in for a YAML import that did not give a rating,
# and the setup screens never asked for one.
OLD_DEFAULT_RATING_WATTS = 3000


def without_default_rating(data) -> dict:
    """Entry data without a rating that is only the old default."""
    return {
        key: value
        for key, value in data.items()
        if not (key == "rating_watts" and value == OLD_DEFAULT_RATING_WATTS)
    }


def renamed_unique_id(unique_id: str | None) -> str | None:
    """The new unique id for an old one, or None if it needs no change."""
    for old, new in RENAMED_KEYS.items():
        if unique_id and unique_id.endswith("_" + old):
            return unique_id[: -len(old)] + new
    return None


def migration_target(
    unique_id: str | None, is_taken: Callable[[str], bool]
) -> tuple[str | None, bool]:
    """The new unique id to use, and whether the rename was skipped as already taken."""
    new = renamed_unique_id(unique_id)
    if new is None:
        return None, False
    if is_taken(new):
        return None, True
    return new, False
