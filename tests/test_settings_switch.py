"""Settings → Subtitles: 1 splitting, 2 display time, 3 lyrics – tick boxes with numbers."""

import pytest

import wt_i18n as i18n

wt = pytest.importorskip("whisper_tool")      # the GUI module – Tkinter itself is not needed for these tests

INLINE = ("split_pause", "max_duration", "min_duration", "max_cps")       # "[x] text [number] unit"
HEADS = ("orphan", "bridge")                                               # "[x] text" over indented rows
SUB_ROWS = ("orphan_limit", "orphan_fill", "bridge_gaps", "line_gap")      # "text [number] unit"
SECTIONS = ("split", "time", "lyrics")
NUMBERS = [("Layout", k) for k in ("split_pause", "max_duration", "min_duration", "max_cps", "bridge_gaps",
                                   "orphan_limit", "orphan_min_fill")] + [("Lyrics", "line_gap")]


class Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def test_without_tick_the_ini_gets_zero():
    assert wt.SwitchVar(Var(False), Var("2")).get() == "0"


def test_with_tick_the_number_counts():
    assert wt.SwitchVar(Var(True), Var("2.5")).get() == "2.5"


@pytest.mark.parametrize("key", NUMBERS)
def test_every_number_is_checked_on_saving(key):
    assert key in wt.SettingsDialog.NUMERIC


@pytest.mark.parametrize("code", [c for c, _ in i18n.available()])
def test_every_row_has_its_texts(code):
    i18n.load(code)
    try:
        missing = [k for k in ([f"set.sw_{k}" for k in INLINE + HEADS + SUB_ROWS]
                               + [f"set.sw_{k}_unit" for k in INLINE + SUB_ROWS]
                               + [f"set.sec_{k}" for k in SECTIONS]
                               + [f"tip.{k}" for k in INLINE + SUB_ROWS + ("balance_lines",)])
                   if not i18n.has(k)]
        assert missing == []
    finally:
        i18n.load("en")


def test_time_units_are_written_out():
    # "2–3" alone was unclear (seconds? frames?) – every time value says "seconds"
    i18n.load("de")
    try:
        for k in ("split_pause", "max_duration", "min_duration", "bridge_gaps", "line_gap"):
            assert "Sekunden" in i18n.T(f"set.sw_{k}_unit"), k
    finally:
        i18n.load("en")


def test_splitting_is_not_called_a_display_time():
    # "steht höchstens 7 Sekunden" sounded like display time – it splits long sentences
    i18n.load("de")
    try:
        assert "teilen" in i18n.T("set.sw_max_duration") and "Sprache" in i18n.T("set.sw_max_duration_unit")
    finally:
        i18n.load("en")
