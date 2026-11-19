from conftest import load

energy = load("energy")


def test_normal_gap_is_credited():
    assert energy.gap_energy_wh(True, 3000, 60, 150) == 50.0


def test_off_is_not_credited():
    assert energy.gap_energy_wh(False, 3000, 60, 150) == 0.0
    assert energy.gap_energy_wh(None, 3000, 60, 150) == 0.0


def test_outage_gap_is_not_credited():
    assert energy.gap_energy_wh(True, 3000, 3 * 3600, 150) == 0.0


def test_gap_at_the_limit_is_credited():
    assert energy.gap_energy_wh(True, 3600, 150, 150) == 150.0

