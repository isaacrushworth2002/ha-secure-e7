import asyncio

import pytest

from conftest import FakeHass, make_coordinator

KEY = bytes(16)
R_APP = bytes(range(16))
R_DEV = bytes(range(16, 32))


def framed(payload):
    return bytes([0, len(payload)]) + payload


def handshake(coordinator, env, replies):
    """Run _four_pass against scripted device replies, with a fixed app nonce."""
    module = env.module
    module.secrets = type("S", (), {"token_bytes": staticmethod(lambda n: R_APP)})
    queue = list(replies)

    async def txrx(packets, wait=0):
        return queue.pop(0)

    coordinator._txrx = txrx
    return asyncio.run(coordinator._four_pass())


def good_replies(env):
    enc = env.module.aes_encrypt
    return [
        framed(bytes([2, 0, 16, 0]) + R_DEV),
        framed(bytes([4, 0, 16, 0]) + enc(KEY, R_APP)),
    ]


def test_a_correct_handshake_passes(coordinator_env):
    c = make_coordinator(coordinator_env)
    assert handshake(c, coordinator_env, good_replies(coordinator_env)) is None


@pytest.mark.parametrize(
    "first",
    [b"", framed(b"\x02\x00"), framed(bytes([9, 0, 16, 0]) + R_DEV), framed(bytes([2, 0, 16, 0]) + R_DEV[:5])],
)
def test_a_garbled_first_reply_is_not_an_auth_failure(coordinator_env, first):
    c = make_coordinator(coordinator_env)
    with pytest.raises(coordinator_env.update_failed) as err:
        handshake(c, coordinator_env, [first])
    assert not isinstance(err.value, coordinator_env.module.AuthFailed)


def test_a_garbled_second_reply_is_not_an_auth_failure(coordinator_env):
    c = make_coordinator(coordinator_env)
    first = good_replies(coordinator_env)[0]
    with pytest.raises(coordinator_env.update_failed) as err:
        handshake(c, coordinator_env, [first, framed(bytes([4, 0, 16, 0]) + b"short")])
    assert not isinstance(err.value, coordinator_env.module.AuthFailed)


def test_a_well_formed_wrong_answer_is_an_auth_failure(coordinator_env):
    c = make_coordinator(coordinator_env)
    first = good_replies(coordinator_env)[0]
    wrong = coordinator_env.module.aes_encrypt(KEY, bytes(16))
    with pytest.raises(coordinator_env.module.AuthFailed):
        handshake(c, coordinator_env, [first, framed(bytes([4, 0, 16, 0]) + wrong)])


def update(coordinator):
    return asyncio.run(coordinator._async_update_data())


def failing_with(coordinator, error):
    async def poll_once():
        raise error

    coordinator._poll_once = poll_once


def test_reauth_needs_three_auth_failures_even_before_the_first_success(coordinator_env):
    c = make_coordinator(coordinator_env)
    failing_with(c, coordinator_env.module.AuthFailed("no"))
    for _ in range(2):
        with pytest.raises(coordinator_env.update_failed):
            update(c)
    with pytest.raises(coordinator_env.exceptions.ConfigEntryAuthFailed):
        update(c)


def test_auth_failures_add_up_across_setup_retries(coordinator_env):
    hass = FakeHass()
    for attempt in range(3):
        c = make_coordinator(coordinator_env, hass=hass)
        failing_with(c, coordinator_env.module.AuthFailed("no"))
        expected = (
            coordinator_env.exceptions.ConfigEntryAuthFailed
            if attempt == 2
            else coordinator_env.update_failed
        )
        with pytest.raises(expected):
            update(c)
    assert hass.data["secure_e7"]["auth_failures"] == {}


def test_other_failures_never_ask_for_reauth(coordinator_env):
    c = make_coordinator(coordinator_env)
    failing_with(c, coordinator_env.update_failed("Timed out waiting for device reply"))
    for _ in range(10):
        with pytest.raises(coordinator_env.update_failed):
            update(c)


def test_a_plain_home_assistant_error_is_counted_and_becomes_update_failed(coordinator_env):
    c = make_coordinator(coordinator_env)
    failing_with(c, coordinator_env.exceptions.HomeAssistantError("odd"))
    with pytest.raises(coordinator_env.update_failed):
        update(c)
    assert c._health.failures == 1


def test_a_good_poll_clears_the_stored_auth_count(coordinator_env):
    c = make_coordinator(coordinator_env)
    failing_with(c, coordinator_env.module.AuthFailed("no"))
    with pytest.raises(coordinator_env.update_failed):
        update(c)
    assert c._auth_counts == {"entry1": 1}

    async def poll_once():
        return {"x": 1}

    c._poll_once = poll_once
    c._start_slow_refresh = lambda: None
    assert update(c) == {"x": 1}
    assert c._auth_counts == {}
