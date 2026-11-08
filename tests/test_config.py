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


# ─── own model per language: precision and prompt ─────────────────────────────

def own_models(tmp_path):
    ini = tmp_path / "whisper_tool.ini"
    ini.write_text("[Whisper]\nmodel = large-v3\ncompute_type = float16\n"
                   "model.de = primeline/whisper-large-v3-german\ncompute_type.de = bfloat16\nuse_prompt.de = 0\n"
                   "compute_type.tr = int8\nuse_prompt.tr = 0\n"                 # without model.tr: no effect
                   "[Prompt]\nde = Rede im Bundestag.\ntr = Türkçe konuşma.\n", encoding="utf-8")
    return core.Config(ini)


def test_precision_and_prompt_belong_to_the_own_model(tmp_path):
    cfg = own_models(tmp_path)
    assert cfg.model_for("de") == ("primeline/whisper-large-v3-german", "bfloat16", False)
    assert cfg.model_for("tr") == ("large-v3", "float16", True)
    assert cfg.model_for("en") == ("large-v3", "float16", True)


def test_own_model_without_prompt_gets_none_not_even_a_custom_one(tmp_path):
    cfg = own_models(tmp_path)
    for mode in ("ini", "custom"):
        opts = core.JobOptions(prompt_mode=mode, custom_prompt="Eigener Text")
        assert core.job_prompt(cfg, "de", opts)[0] == ""
    assert core.job_prompt(cfg, "tr", core.JobOptions(prompt_mode="ini"))[0] == "Türkçe konuşma."
    assert core.job_prompt(cfg, "tr", core.JobOptions(prompt_mode="none"))[0] == ""


def test_own_model_is_on_without_use_model(tmp_path):
    assert own_models(tmp_path).models_off == set()


def test_own_model_switched_off_keeps_its_entries_but_uses_the_default(tmp_path):
    ini = own_models(tmp_path).path
    ini.write_text(ini.read_text(encoding="utf-8").replace("use_prompt.de = 0\n", "use_prompt.de = 0\nuse_model.de = 0\n"),
                   encoding="utf-8")
    cfg = core.Config(ini)
    assert cfg.models["de"] == "primeline/whisper-large-v3-german"
    assert cfg.model_for("de") == ("large-v3", "float16", True)
    assert core.job_prompt(cfg, "de", core.JobOptions(prompt_mode="ini"))[0] == "Rede im Bundestag."


@pytest.mark.parametrize("compute, device, cap, expected", [
    ("bfloat16", "cuda", (8, 9), ("bfloat16", "")),             # RTX 40xx
    ("bfloat16", "cuda", (7, 5), ("float16", "core.bf16_gpu")),  # RTX 20xx
    ("bfloat16", "cpu", None, ("float32", "core.bf16_cpu")),
    ("float16", "cpu", None, ("float32", "")),
    ("int8_float16", "cuda", (6, 1), ("int8_float16", "")),
])
def test_compute_type_that_the_device_can_run(compute, device, cap, expected):
    assert core.effective_compute(compute, device, cap) == expected


def test_both_compute_lists_offer_bfloat16():
    assert "bfloat16" in core.COMPUTE_TYPES and "bfloat16" in EXAMPLE.read_text(encoding="utf-8")


def test_system_check_lists_only_own_models_that_are_on(tmp_path):
    ini = own_models(tmp_path).path
    ini.write_text(ini.read_text(encoding="utf-8").replace("use_prompt.de = 0\n", "use_prompt.de = 0\nuse_model.de = 0\n")
                   + "[System]\nffmpeg_path = ffmpeg\n", encoding="utf-8")
    texts = [text for _, text in core.check_environment(core.Config(ini))]
    assert not any("primeline" in t for t in texts)
    assert any(t.startswith(core.T("set.model") + ":") for t in texts)


def test_ini_without_close_plugin_closes_the_plugin_window_after_10_seconds(tmp_path):
    ini = tmp_path / "old.ini"
    ini.write_text("[Resolve]\ncountdown = 10\n", encoding="utf-8")
    cfg = core.Config(ini)
    assert (cfg.resolve_close_plugin, cfg.resolve_close_warning) == (10, False)
    template = core.Config(EXAMPLE)
    assert (template.resolve_close_plugin, template.resolve_close_warning) == (10, False)


def test_ini_with_characters_per_line_keeps_its_subtitles(tmp_path):
    ini = tmp_path / "old.ini"
    ini.write_text("[Layout]\ndefault_chars = 42\ndefault_lines = 2\n", encoding="utf-8")
    cfg = core.Config(ini)
    assert cfg.default_cue_chars == 84 and cfg.layout().max_chars == 42
    assert (cfg.layout(54, 1).max_chars, cfg.layout(54, 2).max_chars, cfg.layout(55, 2).max_chars) == (54, 27, 27)
    ini.write_text("[Layout]\ndefault_chars = 42\ndefault_cue_chars = 60\ndefault_lines = 2\n", encoding="utf-8")
    assert core.Config(ini).default_cue_chars == 60
