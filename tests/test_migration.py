import pytest

from conftest import load

migration = load("migration")


def test_the_old_default_rating_is_dropped_and_any_other_kept():
    old = {"mac": "AA", "rating_watts": 3000}
    assert migration.without_default_rating(old) == {"mac": "AA"}
    assert migration.without_default_rating({"rating_watts": 2000}) == {"rating_watts": 2000}
    assert migration.without_default_rating({"mac": "AA"}) == {"mac": "AA"}
    assert old["rating_watts"] == 3000


def test_follow_schedule_unique_id_becomes_timed_boost():
    assert migration.renamed_unique_id("AA:BB:CC:11:22:33_follow_schedule") == "AA:BB:CC:11:22:33_timed_boost"


@pytest.mark.parametrize(
    "unique_id",
    ["AA:BB:CC:11:22:33_timed_boost", "AA:BB:CC:11:22:33_hot_water", "follow_schedule", "", None],
)
def test_other_unique_ids_are_left_alone(unique_id):
    assert migration.renamed_unique_id(unique_id) is None


def test_a_rename_is_made_when_the_new_id_is_free():
    assert migration.migration_target("AA_follow_schedule", lambda uid: False) == ("AA_timed_boost", False)


def test_a_rename_is_skipped_when_the_new_id_is_already_registered():
    taken = {"AA_timed_boost"}
    assert migration.migration_target("AA_follow_schedule", taken.__contains__) == (None, True)


def test_no_rename_is_not_reported_as_skipped():
    assert migration.migration_target("AA_hot_water", lambda uid: True) == (None, False)
