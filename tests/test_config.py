"""whisper_tool.ini: reading values and writing them back without losing comments."""

import shutil

import wt_core as core
from helpers import ROOT

EXAMPLE = ROOT / "whisper_tool.example.ini"


def not_ffmpeg(warnings):
    return [w for w in warnings if "ffmpeg" not in w]


def test_example_ini_loads_without_warnings():
    cfg = core.Config(EXAMPLE)
    assert not_ffmpeg(cfg.warnings) == []
    assert cfg.min_duration == 1.0 and cfg.max_cps == 17.0
    lay = cfg.layout()
    assert (lay.max_chars, lay.max_lines, lay.min_duration, lay.max_cps) == (42, 2, 1.0, 17.0)


def test_old_ini_without_new_keys_keeps_old_behaviour(tmp_path):
    ini = tmp_path / "old.ini"
    ini.write_text("[Layout]\nmax_duration = 7\n", encoding="utf-8")
    cfg = core.Config(ini)
    assert cfg.min_duration == 0.0 and cfg.max_cps == 0.0


def test_invalid_value_gives_default_and_warning(tmp_path):
    ini = tmp_path / "bad.ini"
    ini.write_text("[Layout]\nmax_cps = viele\n", encoding="utf-8")
    cfg = core.Config(ini)
    assert cfg.max_cps == 0.0
    assert any("max_cps" in w for w in cfg.warnings)


def test_language_rules_from_ini():
    rules = core.Config(EXAMPLE).rules("de")
    assert "ähm" in rules.fillers and "der" in rules.dangling and rules.ordinal


def test_save_ini_changes_only_the_value(tmp_path):
    ini = tmp_path / "whisper_tool.ini"
    shutil.copy(EXAMPLE, ini)
    before = ini.read_text(encoding="utf-8").splitlines()
    core.save_ini(ini, {("Layout", "max_cps"): "20"})
    after = ini.read_text(encoding="utf-8").splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after)
    assert changed == [("max_cps      = 17", "max_cps      = 20")]       # alignment kept
    assert core.Config(ini).max_cps == 20.0
    assert (tmp_path / "whisper_tool.ini.bak").is_file()


def test_save_ini_adds_missing_key_to_its_section(tmp_path):
    ini = tmp_path / "t.ini"
    ini.write_text("; my comment\n[Layout]\nmax_duration = 7\n\n[Gaps]\nenabled = 1\n", encoding="utf-8")
    core.save_ini(ini, {("Layout", "min_duration"): "1"})
    text = ini.read_text(encoding="utf-8")
    assert "; my comment" in text
    assert text.index("min_duration = 1") < text.index("[Gaps]")
    assert core.Config(ini).min_duration == 1.0


def test_suffix_placeholders():
    assert core.fill_suffix(".{model}.srt", r"C:\models\whisper-tr", "tr") == ".whisper-tr.srt"
    assert core.fill_suffix(".{model}.{lang}.srt", "large-v3", "de") == ".large-v3.de.srt"
