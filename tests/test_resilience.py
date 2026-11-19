from conftest import load

PollHealth = load("resilience").PollHealth


def test_no_data_yet_means_the_first_failure_is_raised():
    assert PollHealth().failure(100) is False


def test_three_failures_in_a_row_are_tolerated_then_the_fourth_raises():
    h = PollHealth()
    h.success(0)
    assert [h.failure(t) for t in (60, 120, 180, 240)] == [True, True, True, False]


def test_a_success_resets_the_count():
    h = PollHealth()
    h.success(0)
    h.failure(60)
    h.failure(120)
    assert h.success(180) == 2
    assert h.failures == 0
    assert h.failure(240) is True


def test_stale_data_is_dropped_after_five_minutes():
    h = PollHealth()
    h.success(0)
    assert h.failure(300) is True
    assert h.failure(301) is False
