"""
Minimum display time and reading speed (characters per second).
Only sentence mode is extended – word by word must stay exactly as long as spoken.
"""

import pytest

import wt_core as core
from helpers import make_result

RULES = core.LangRules(lang="de")


def layout(**kw) -> core.Layout:
    return core.Layout(**{"max_chars": 42, "max_lines": 2, **kw})


# ── extend_reading_time ───────────────────────────────────────────────────────

def test_short_subtitle_is_extended_to_minimum():
    cues = core.extend_reading_time([(0.0, 0.3, ["Danke."])], layout(min_duration=1.0))
    assert cues == [(0.0, 1.0, ["Danke."])]


def test_start_and_text_never_change():
    before = [(0.0, 0.3, ["Ja."]), (5.0, 5.2, ["Nein."])]
    after = core.extend_reading_time(before, layout(min_duration=1.0, max_cps=17))
    assert [(s, lines) for s, _, lines in after] == [(s, lines) for s, _, lines in before]


def test_never_runs_into_the_next_subtitle():
    cues = core.extend_reading_time([(0.0, 0.3, ["Ja."]), (0.6, 1.8, ["Weiter so."])], layout(min_duration=1.0))
    assert cues[0][1] == pytest.approx(0.6 - core.CUE_GAP)


def test_never_shortens_a_subtitle():
    # WhisperX can deliver overlapping words – the end must not move backwards
    cues = core.extend_reading_time([(0.0, 0.5, ["Ja."]), (0.4, 1.8, ["Weiter so."])], layout(min_duration=1.0))
    assert cues[0][1] == 0.5


def test_max_duration_is_no_display_limit():
    # "split long sentences" decides which words form a subtitle – how long it stays is display time
    cues = core.extend_reading_time([(0.0, 0.3, ["x" * 80]), (20.0, 21.0, ["next"])],
                                    layout(max_cps=10, max_duration=3))
    assert cues[0][1] == pytest.approx(8.0)


def test_last_subtitle_stops_at_end_of_audio():
    cues = core.extend_reading_time([(9.5, 9.8, ["Ende."])], layout(min_duration=1.0), duration=10.0)
    assert cues[0][1] == pytest.approx(10.0)


def test_reading_speed_extends_long_subtitle():
    text = "Dieser Text hat genau 34 Zeichen.."              # 34 characters
    assert core.cue_chars([text]) == 34
    cues = core.extend_reading_time([(0.0, 1.0, [text])], layout(max_cps=17))
    assert cues[0][1] == pytest.approx(2.0)


def test_line_breaks_do_not_count_as_characters():
    assert core.cue_chars(["abc", "de"]) == 5


def test_switched_off_changes_nothing():
    before = [(0.0, 0.1, ["Ja."]), (0.2, 0.3, ["x" * 80])]
    assert core.extend_reading_time(before, layout()) == before


# ── fast_cues (warnings) ──────────────────────────────────────────────────────

def test_too_fast_without_pause_is_reported():
    cues = [(0.0, 1.0, ["x" * 40]), (1.05, 3.0, ["Weiter."])]
    extended = core.extend_reading_time(cues, layout(max_cps=17))
    fast = core.fast_cues(extended, 17)
    assert len(fast) == 1
    assert fast[0][0] == 0.0 and fast[0][2] > 17


def test_extended_in_time_is_not_reported():
    extended = core.extend_reading_time([(0.0, 1.0, ["x" * 40])], layout(max_cps=17))
    assert core.fast_cues(extended, 17) == []


def test_no_reading_speed_check_when_off():
    assert core.fast_cues([(0.0, 0.1, ["x" * 80])], 0) == []


# ── Modes: only sentence mode is extended ─────────────────────────────────────

LAY = layout(min_duration=1.0, max_cps=17)


def quick_result():
    # every word 0.2 s long, 0.25 s apart – fast speech, with a pause before "Danke."
    return make_result("Ja. Gut. Danke.", step=0.25, length=0.2, pauses={2: 2.0})


def test_sentence_mode_is_extended():
    cues, mode = core.build_cues(quick_result(), "sentence", RULES, LAY, duration=10.0)
    assert mode == "sentence"
    assert all(end - start >= 1.0 - 1e-9 or nxt[0] - end <= core.CUE_GAP + 1e-9
               for (start, end, _), nxt in zip(cues, cues[1:] + [(99, 0, [])]))
    assert cues[-1][1] - cues[-1][0] == pytest.approx(1.0)         # "Danke." – pause after it


def test_word_mode_stays_exactly_as_spoken():
    result = quick_result()
    cues, mode = core.build_cues(result, "word", RULES, LAY, duration=10.0)
    assert mode == "word"
    spoken = [(w["start"], w["end"]) for w in result["segments"][0]["words"]]
    assert [(s, e) for s, e, _ in cues] == spoken


def test_raw_mode_stays_unchanged():
    result = quick_result()
    cues, _ = core.build_cues(result, "raw", RULES, LAY, duration=10.0)
    seg = result["segments"][0]
    assert cues == [(seg["start"], seg["end"], [seg["text"]])]


def test_raw_fallback_is_not_extended():
    result = {"segments": [{"start": 1.0, "end": 1.2, "text": "Ja."}]}
    cues, mode = core.build_cues(result, "sentence", RULES, LAY, duration=10.0)
    assert mode == "raw" and cues == [(1.0, 1.2, ["Ja."])]


# ── Log messages of the engine ────────────────────────────────────────────────

def test_engine_warns_about_fast_subtitles(tmp_path):
    ini = tmp_path / "t.ini"
    ini.write_text("[Layout]\nmax_cps = 17\n", encoding="utf-8")
    log = []
    engine = core.Engine(core.Config(ini), log=log.append)
    n = engine._report_fast([(0.0, 1.0, ["x" * 40]), (1.05, 3.0, ["ok"])])
    assert n == 1
    assert any("faster than 17 characters/s" in line for line in log)
    assert any("0:00.0 – 0:01.0" in line for line in log)
