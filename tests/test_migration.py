import pytest

from conftest import load

migration = load("migration")


def test_follow_schedule_unique_id_becomes_timed_boost():
    assert migration.renamed_unique_id("AA:BB:CC:11:22:33_follow_schedule") == "AA:BB:CC:11:22:33_timed_boost"


@pytest.mark.parametrize(
    "unique_id",
    ["AA:BB:CC:11:22:33_timed_boost", "AA:BB:CC:11:22:33_hot_water", "follow_schedule", "", None],
)
def test_other_unique_ids_are_left_alone(unique_id):
    assert migration.renamed_unique_id(unique_id) is None
