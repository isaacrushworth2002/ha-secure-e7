from conftest import load

redact = load("redact").redact
REDACTED = "**REDACTED**"


def test_key_mac_and_gmi_are_hidden():
    data = {"mac": "AA:BB", "key": "00" * 16, "gmi": "1234", "boost_minutes": 60}
    assert redact(data) == {
        "mac": REDACTED,
        "key": REDACTED,
        "gmi": REDACTED,
        "boost_minutes": 60,
    }


def test_nested_values_are_hidden_and_the_input_is_untouched():
    data = {"a": [{"key": "secret", "x": 1}], "b": {"gmi": "9"}}
    assert redact(data) == {"a": [{"key": REDACTED, "x": 1}], "b": {"gmi": REDACTED}}
    assert data["a"][0]["key"] == "secret"


def test_none_and_scalars_pass_through():
    assert redact(None) is None
    assert redact(3) == 3
