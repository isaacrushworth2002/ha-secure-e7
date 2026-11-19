"""Entity unique id renames between releases, kept free of Home Assistant."""
from __future__ import annotations

# Old key -> new key. The entity registry keeps the entity id, so automations
# and dashboards that use it carry on working.
RENAMED_KEYS = {"follow_schedule": "timed_boost"}


def renamed_unique_id(unique_id: str | None) -> str | None:
    """The new unique id for an old one, or None if it needs no change."""
    for old, new in RENAMED_KEYS.items():
        if unique_id and unique_id.endswith("_" + old):
            return unique_id[: -len(old)] + new
    return None
