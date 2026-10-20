from conftest import load

backoff = load("backoff")
poll_interval = backoff.poll_interval


def test_healthy_polls_at_the_normal_rate():
    assert poll_interval(0) == 60


def test_one_or_two_failures_keep_the_normal_rate():
    assert poll_interval(1) == 60
    assert poll_interval(2) == 60


def test_backs_off_to_2_then_5_then_10_minutes():
    assert [poll_interval(n) for n in (3, 4, 5)] == [120, 300, 600]


def test_never_waits_longer_than_10_minutes():
    assert poll_interval(50) == 600
