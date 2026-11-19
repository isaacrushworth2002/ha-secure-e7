import asyncio

from conftest import load

resilience = load("resilience")
PollHealth = resilience.PollHealth
with_retries = resilience.with_retries


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


def test_one_auth_failure_is_enough_before_the_first_success():
    h = PollHealth()
    assert h.reauth_needed is False
    h.failure(10, auth=True)
    assert h.reauth_needed is True


def test_two_auth_failures_in_a_row_are_needed_after_a_success():
    h = PollHealth()
    h.success(0)
    h.failure(60, auth=True)
    assert h.reauth_needed is False
    h.failure(120, auth=True)
    assert h.reauth_needed is True


def test_another_kind_of_failure_or_a_success_clears_the_auth_count():
    h = PollHealth()
    h.success(0)
    h.failure(60, auth=True)
    h.failure(120)
    h.failure(180, auth=True)
    assert h.reauth_needed is False
    h.success(240)
    assert h.auth_failures == 0


class Transient(Exception):
    pass


class Fatal(Transient):
    pass


def run_retries(outcomes, retries=1):
    calls = []

    async def call():
        calls.append(1)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    try:
        result = asyncio.run(with_retries(call, retries, (Transient,), (Fatal,)))
    except Exception as err:  # noqa: BLE001
        result = err
    return result, len(calls)


def test_a_transient_failure_is_retried_once():
    assert run_retries([Transient(), "ok"]) == ("ok", 2)


def test_the_last_error_is_raised_when_every_try_fails():
    result, calls = run_retries([Transient("a"), Transient("b")])
    assert str(result) == "b"
    assert calls == 2


def test_fatal_errors_are_not_retried():
    result, calls = run_retries([Fatal(), "ok"])
    assert isinstance(result, Fatal)
    assert calls == 1


def test_other_errors_pass_straight_through():
    result, calls = run_retries([KeyError("x"), "ok"])
    assert isinstance(result, KeyError)
    assert calls == 1


def test_two_retries_allow_three_tries():
    assert run_retries([Transient(), Transient(), "ok"], retries=2) == ("ok", 3)
