"""whisper_tool.ini: reading values and writing them back without losing comments."""

import shutil

import pytest

import wt_core as core
from helpers import ROOT

EXAMPLE = ROOT / "whisper_tool.example.ini"


def not_ffmpeg(warnings):
    return [w for w in warnings if "ffmpeg" not in w]


def test_example_ini_loads_without_warnings():
    cfg = core.Config(EXAMPLE)
    assert not_ffmpeg(cfg.warnings) == []
    assert (cfg.pause, cfg.smooth, cfg.pause_hold) == (2.0, True, 1.0)
    lay = cfg.layout()
    assert (lay.max_chars, lay.max_lines, lay.pause, lay.max_duration, lay.balance) == (42, 2, 2.0, 7.0, True)


def test_old_ini_without_balance_keeps_old_line_breaks(tmp_path):
    ini = tmp_path / "old.ini"
    ini.write_text("[Layout]\nmax_duration = 7\n", encoding="utf-8")
    assert core.Config(ini).layout().balance is False


def test_invalid_value_gives_default_and_warning(tmp_path):
    ini = tmp_path / "bad.ini"
    ini.write_text("[Layout]\npause = viele\n", encoding="utf-8")
    cfg = core.Config(ini)
    assert cfg.pause == 2.0
    assert any("pause" in w for w in cfg.warnings)


def test_language_rules_from_ini():
    rules = core.Config(EXAMPLE).rules("de")
    assert "ähm" in rules.fillers and "der" in rules.dangling and rules.ordinal


def test_save_ini_changes_only_the_value(tmp_path):
    ini = tmp_path / "whisper_tool.ini"
    shutil.copy(EXAMPLE, ini)
    before = ini.read_text(encoding="utf-8").splitlines()
    core.save_ini(ini, {("Layout", "pause_hold"): "0.5"})
    after = ini.read_text(encoding="utf-8").splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after)
    assert changed == [("pause_hold = 1", "pause_hold = 0.5")]          # alignment kept
    assert core.Config(ini).pause_hold == 0.5
    assert (tmp_path / "whisper_tool.ini.bak").is_file()


def test_save_ini_adds_missing_key_to_its_section(tmp_path):
    ini = tmp_path / "t.ini"
    ini.write_text("; my comment\n[Layout]\nmax_duration = 7\n\n[Gaps]\nenabled = 1\n", encoding="utf-8")
    core.save_ini(ini, {("Layout", "pause"): "3"})
    text = ini.read_text(encoding="utf-8")
    assert "; my comment" in text
    assert text.index("pause = 3") < text.index("[Gaps]")
    assert core.Config(ini).pause == 3.0


def test_suffix_placeholders():
    assert core.fill_suffix(".{model}.srt", r"C:\models\whisper-tr", "tr") == ".whisper-tr.srt"
    assert core.fill_suffix(".{model}.{lang}.srt", "large-v3", "de") == ".large-v3.de.srt"


def test_score_check_is_off_without_its_section(tmp_path):
    ini = tmp_path / "old.ini"
    ini.write_text("[Gaps]\nenabled = 1\n", encoding="utf-8")                  # INI without [Score]
    cfg = core.Config(ini)
    assert (cfg.score_threshold, cfg.score_srt) == (0.0, True) and not_ffmpeg(cfg.warnings) == []
    assert core.Config(EXAMPLE).score_threshold == 0.4


@pytest.mark.parametrize("raw, value", [("0", 0.0), ("0.05", 0.05), ("0.4", 0.4), ("0.95", 0.95)])
def test_valid_score_thresholds(tmp_path, raw, value):
    ini = tmp_path / "t.ini"
    ini.write_text(f"[Score]\nthreshold = {raw}\nwrite_srt = 0\n", encoding="utf-8")
    cfg = core.Config(ini)
    assert (cfg.score_threshold, cfg.score_srt) == (value, False) and not_ffmpeg(cfg.warnings) == []


@pytest.mark.parametrize("raw", ["0.01", "0.96", "1", "-0.4", "viel", "nan"])
def test_invalid_score_threshold_gives_a_warning_and_off(tmp_path, raw):
    ini = tmp_path / "t.ini"
    ini.write_text(f"[Score]\nthreshold = {raw}\n", encoding="utf-8")
    cfg = core.Config(ini)
    assert cfg.score_threshold == 0.0
    assert any("[Score] threshold" in w for w in cfg.warnings)


@pytest.mark.parametrize("text, gaps, fast", [
    ("[Menu]\ntimeout = 15\n", True, True),                                    # no [Resolve] at all
    ("[Resolve]\nextra_tracks = 1\n", True, True),                             # INI with extra_tracks only
    ("[Resolve]\nextra_tracks = 0\n", False, False),
    ("[Resolve]\nextra_tracks = 0\ntrack_gaps = 1\n", True, False),            # a new key wins, the other follows
    ("[Resolve]\nextra_tracks = 1\ntrack_fast = 0\n", True, False),
    ("[Resolve]\ntrack_gaps = 0\ntrack_fast = 1\n", False, True),
])
def test_track_keys_fall_back_to_extra_tracks(tmp_path, text, gaps, fast):
    ini = tmp_path / "t.ini"
    ini.write_text(text, encoding="utf-8")
    cfg = core.Config(ini)
    assert (cfg.resolve_gaps, cfg.resolve_fast) == (gaps, fast)


def test_example_ini_has_the_new_track_keys_only():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "track_gaps = 1" in text and "track_fast = 1" in text
    assert not any(line.startswith("extra_tracks") for line in text.splitlines())
