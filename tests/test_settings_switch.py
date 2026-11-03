"""Settings dialog: tick box + number ("Close gaps when the pause is shorter than [2] seconds")."""

import pytest

import wt_i18n as i18n

wt = pytest.importorskip("whisper_tool")      # the GUI module – Tkinter itself is not needed for these tests

SWITCHES = ("split_pause", "max_duration", "min_duration", "max_cps", "bridge_gaps")


class Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def test_without_tick_the_ini_gets_zero():
    assert wt.SwitchVar(Var(False), Var("2")).get() == "0"


def test_with_tick_the_number_counts():
    assert wt.SwitchVar(Var(True), Var("2.5")).get() == "2.5"


@pytest.mark.parametrize("key", SWITCHES)
def test_every_switch_is_checked_and_has_texts(key):
    assert ("Layout", key) in wt.SettingsDialog.NUMERIC          # the number is validated on saving
    for code, _ in i18n.available():
        i18n.load(code)
        assert i18n.has(f"set.sw_{key}") and i18n.has(f"set.sw_{key}_unit") and i18n.has(f"tip.{key}")
    i18n.load("en")


def test_units_are_written_out():
    # "2–3" alone was unclear (seconds? frames?) – every time unit says "seconds"
    i18n.load("de")
    assert i18n.T("set.sw_bridge_gaps_unit") == "Sekunden"
    assert "Sekunden" in i18n.T("set.bridge_gaps_note")
    i18n.load("en")
