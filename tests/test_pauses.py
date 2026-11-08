"""
Pauses: only the length of a pause counts.
  up to X seconds   → the segment stays until the next one starts (seamless, no flicker)
  longer than X     → a real pause: the segment stays `hold` seconds, then the screen is empty
"""

import json
import re
import shutil

import pytest

import wt_core as core
from helpers import ROOT, make_result

X, HOLD = 2.0, 1.0


def smooth(cues, duration=None):
    return core.smooth_cues(cues, X, HOLD, duration)


def test_short_pause_is_closed_seamlessly():
    cues = smooth([(0.0, 1.0, ["Eins"]), (1.3, 2.0, ["Zwei"])])
    assert cues[0][1] == 1.3


def test_pause_of_exactly_x_is_still_short():
    assert smooth([(0.0, 1.0, ["a"]), (3.0, 4.0, ["b"])])[0][1] == 3.0


def test_real_pause_holds_then_screen_is_empty():
    cues = smooth([(0.0, 1.0, ["Danke."]), (6.0, 7.0, ["Weiter."])])
    assert cues[0][1] == 2.0                  # 1 s hold
    assert cues[1][0] - cues[0][1] == 4.0     # 4 s empty


def test_starts_never_move():
    before = [(0.0, 0.2, ["a"]), (0.5, 0.6, ["b"]), (5.0, 5.3, ["c"])]
    assert [s for s, _, _ in smooth(before)] == [0.0, 0.5, 5.0]


def test_overlap_is_cut():
    assert smooth([(0.0, 1.5, ["a"]), (1.2, 2.0, ["b"])])[0][1] == 1.2


def test_last_segment_holds_until_end_of_audio_at_most():
    assert smooth([(0.0, 1.0, ["Ende."])], duration=10.0)[0][1] == 2.0
    assert smooth([(9.5, 9.8, ["Ende."])], duration=10.0)[0][1] == 10.0


def test_hold_never_runs_into_the_next_segment():
    # hand-edited INI with hold ≥ pause: still never beyond the next start
    assert core.smooth_cues([(0.0, 1.0, ["a"]), (3.5, 4.0, ["b"])], 2.0, 5.0)[0][1] == 3.5


def test_inside_and_between_sentences_the_same():
    # "das." ends a sentence, "Danach" follows after 0.3 s – a short pause, so it is closed as well
    cues = smooth([(0.0, 0.3, ["euch"]), (0.4, 0.7, ["das."]), (1.0, 1.3, ["Danach"])])
    assert [e for _, e, _ in cues[:2]] == [0.4, 1.0]


# ── settings ──────────────────────────────────────────────────────────────────

def cfg_with(tmp_path, text):
    ini = tmp_path / "t.ini"
    ini.write_text(text, encoding="utf-8")
    return core.Config(ini)


def test_defaults_for_an_ini_without_the_new_keys(tmp_path):
    cfg = cfg_with(tmp_path, "[Layout]\nbridge_gaps = 1\nsplit_pause = 0\n")    # keys of older versions are ignored
    assert (cfg.pause, cfg.smooth, cfg.pause_hold) == (2.0, True, 1.0)


def test_hold_must_be_shorter_than_pause(tmp_path):
    cfg = cfg_with(tmp_path, "[Layout]\npause = 2\npause_hold = 3\n")
    assert cfg.pause_hold == 1.0
    assert any("pause_hold" in w for w in cfg.warnings)


def test_smooth_off_keeps_the_spoken_times(tmp_path):
    cfg = cfg_with(tmp_path, "[Layout]\nsmooth = 0\n")
    cues = [(0.0, 1.0, ["a"]), (1.2, 2.0, ["b"])]
    assert core.display_cues(cues, "word", cfg) == cues


def test_lyrics_keep_their_own_rules(tmp_path):
    cfg = cfg_with(tmp_path, "[Layout]\nsmooth = 1\n")
    cues = [(0.0, 1.0, ["Zeile 1"]), (1.05, 2.0, ["Zeile 2"])]
    assert core.display_cues(cues, "lyrics", cfg) == cues
    for mode in ("sentence", "word", "raw"):
        assert core.display_cues(cues, mode, cfg)[0][1] == 1.05


def test_lyrics_seamless_with_gap_zero():
    cues = [[0.0, 1.0, ["Zeile 1"]], [1.5, 2.0, ["Zeile 2"]]]
    core.extend_line_ends(cues, duration=10.0, hold=1.0, gap=0.0)
    assert cues[0][1] == 1.5


# ── sentence mode: a real pause starts a new subtitle ─────────────────────────

RULES = core.LangRules(lang="de")


def sentence(result, pause):
    lay = core.Layout(max_chars=42, max_lines=2, pause=pause)
    return [" ".join(l) for _, _, l in core.build_cues(result, "sentence", RULES, lay)[0]]


def test_real_pause_splits_short_pause_does_not():
    assert sentence(make_result("Erst das hier und dann das", pauses={4: 2.5}), 2) == ["Erst das hier und", "dann das"]
    assert sentence(make_result("Erst das hier und dann das", pauses={4: 1.5}), 2) == ["Erst das hier und dann das"]


# ── whole pipeline: WhisperX JSON → SRT file (no GPU needed) ───────────────────

def run(tmp_path, mode, **layout):
    ini = tmp_path / "whisper_tool.ini"
    shutil.copy(ROOT / "whisper_tool.example.ini", ini)
    core.save_ini(ini, {("Layout", k): str(v) for k, v in layout.items()})
    src = tmp_path / "talk.json"
    # 0.15 s between the words, 4 s pause before "Danach"
    result = make_result("Heute zeige ich euch das. Danach Pause.", step=0.5, length=0.35, pauses={5: 4.0})
    src.write_text(json.dumps(result), encoding="utf-8")
    cfg = core.Config(ini)
    opts = core.JobOptions.from_config(cfg)
    opts.mode = mode
    info = core.Engine(cfg, log=lambda m: None).process(src, opts)
    text = info["output"].read_text(encoding="utf-8")
    times = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", text)
    return [(secs(a), secs(b)) for a, b in times], result


def secs(t):
    h, m, s = t.replace(",", ".").split(":")
    return round(int(h) * 3600 + int(m) * 60 + float(s), 3)


@pytest.mark.parametrize("mode", ["sentence", "word", "raw"])
def test_pipeline_seamless_or_visible_pause(tmp_path, mode):
    times, _ = run(tmp_path, mode)
    gaps = [round(b[0] - a[1], 3) for a, b in zip(times, times[1:])]
    # either seamless (0) or a real pause that stays visible (4.15 s pause − 1 s hold)
    assert all(g == 0 or g == pytest.approx(3.15) for g in gaps), gaps


def test_word_mode_in_the_srt(tmp_path):
    times, result = run(tmp_path, "word")
    spoken = [(w["start"], w["end"]) for w in result["segments"][0]["words"]]
    assert [s for s, _ in times] == pytest.approx([s for s, _ in spoken])         # starts exact
    assert times[3][1] == times[4][0]                                               # "euch" → "das." seamless
    assert times[4][1] == pytest.approx(spoken[4][1] + 1.0)                         # "das." + 1 s hold
    assert times[-1][1] == pytest.approx(spoken[-1][1] + 1.0)                       # last word + 1 s


def test_pipeline_smooth_off_is_exactly_as_spoken(tmp_path):
    times, result = run(tmp_path, "word", smooth=0)
    assert times == [pytest.approx(w) for w in [(w["start"], w["end"]) for w in result["segments"][0]["words"]]]


# ── raw: WhisperX segments unchanged, except at a real pause inside a segment ─

def raw(result, pause=X):
    return core.cues_raw(result, RULES, pause)


def test_raw_splits_a_segment_at_a_real_pause():
    result = make_result("Heute zeige ich euch das. Danach Pause.", pauses={5: 4.0})
    seg = result["segments"][0]
    cues = raw(result)
    assert [c[2] for c in cues] == [["Heute zeige ich euch das."], ["Danach Pause."]]
    assert cues[0][0] == seg["start"] and cues[-1][1] == seg["end"]
    assert cues[1][0] == seg["words"][5]["start"]


def test_raw_keeps_a_segment_without_real_pause():
    result = make_result("Heute zeige ich euch das.", pauses={3: 1.5})
    seg = result["segments"][0]
    assert raw(result) == [(seg["start"], seg["end"], [seg["text"]])]


def test_raw_without_word_times_stays_as_it_is():
    result = {"segments": [{"start": 0.0, "end": 9.0, "text": "Ganz ohne Wort-Zeiten"}]}
    assert raw(result) == [(0.0, 9.0, ["Ganz ohne Wort-Zeiten"])]


def test_raw_parts_get_the_replacements():
    rules = core.LangRules(lang="de", repl_phrase=core.compile_replacements([("Open AI", "OpenAI")]))
    result = make_result("Open AI ist da. Danach mehr.", pauses={4: 3.0})
    assert [c[2][0] for c in core.cues_raw(result, rules, X)] == ["OpenAI ist da.", "Danach mehr."]


def test_pipeline_raw_screen_is_empty_during_the_pause(tmp_path):
    times, result = run(tmp_path, "raw")
    words = result["segments"][0]["words"]
    assert len(times) == 2                                                          # split at the 4 s pause
    assert times[0][1] == pytest.approx(words[4]["end"] + 1.0)                      # "das." + 1 s hold
    assert times[1][0] == pytest.approx(words[5]["start"])                          # "Danach" exactly on time


def test_smooth_display_chosen_for_the_job_wins_over_the_ini(tmp_path):
    ini = tmp_path / "whisper_tool.ini"
    ini.write_text("[Layout]\nsmooth = 0\npause = 2\npause_hold = 5\n", encoding="utf-8")
    cfg = core.Config(ini)
    cues = [(0.0, 1.0, ["a"]), (1.5, 2.0, ["b"]), (8.0, 9.0, ["c"])]
    assert core.display_cues(cues, "sentence", cfg) == cues
    on = core.display_cues(cues, "sentence", cfg, smooth=True)
    assert on[0][1] == 1.5 and on[1][1] == 3.0          # held up to the next one; real pause: hold of pause / 2
    assert core.display_cues(cues, "lyrics", cfg, smooth=True) == cues
