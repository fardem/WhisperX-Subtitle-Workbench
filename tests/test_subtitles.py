"""Sentence, word and raw mode – the subtitle rules in wt_core."""

import pytest

import wt_core as core
from helpers import make_result

DE = core.LangRules(lang="de", fillers=frozenset({"ähm"}), dangling=frozenset({"das", "der", "die"}),
                    ordinal=True)
EN = core.LangRules(lang="en")


def layout(**kw) -> core.Layout:
    return core.Layout(**{"max_chars": 42, "max_lines": 2, **kw})


def sentence(result, lay=None, rules=DE):
    cues, mode = core.build_cues(result, "sentence", rules, lay or layout())
    assert mode == "sentence"
    return cues


def texts(cues) -> list[str]:
    return [" / ".join(lines) for _, _, lines in cues]


# ── Sentences ─────────────────────────────────────────────────────────────────

def test_sentence_end_starts_new_subtitle():
    assert texts(sentence(make_result("Das ist gut. Das ist schlecht."))) == ["Das ist gut.", "Das ist schlecht."]


def test_german_ordinal_is_not_a_sentence_end():
    assert texts(sentence(make_result("Am 6. November kommt er."))) == ["Am 6. November kommt er."]


def test_ordinal_rule_only_where_switched_on():
    assert texts(sentence(make_result("Number 7. Next one."), rules=EN)) == ["Number 7.", "Next one."]


def test_line_length_and_line_count_limits():
    text = "Wir haben heute sehr viele Wörter die alle in Untertitel passen müssen und zwar ohne Ende"
    cues = sentence(make_result(text), layout(max_chars=20, max_lines=2))
    assert all(len(line) <= 20 for _, _, lines in cues for line in lines)
    assert all(len(lines) <= 2 for _, _, lines in cues)
    assert " ".join(" ".join(lines) for _, _, lines in cues) == text      # no word lost


def test_word_without_timestamp_is_kept():
    result = make_result("Im Jahr 2024 war es so.")
    w = result["segments"][0]["words"][2]
    del w["start"], w["end"]                     # WhisperX often gives numbers no time
    cues = sentence(result)
    assert texts(cues) == ["Im Jahr 2024 war es so."]
    filled = core.collect_words(result)[2]
    assert filled["start"] <= filled["end"]


def test_fillers_are_removed():
    assert texts(sentence(make_result("Das ähm ist gut."))) == ["Das ist gut."]


def test_filler_passes_its_sentence_end_on():
    assert texts(sentence(make_result("Das ist gut ähm. Weiter geht es."))) == ["Das ist gut.", "Weiter geht es."]


def test_dangling_article_moves_to_next_subtitle():
    cues = sentence(make_result("Wir sehen heute das neue Haus."), layout(max_chars=20, max_lines=1))
    assert texts(cues) == ["Wir sehen heute", "das neue Haus."]


def test_dangling_word_moves_and_following_subtitles_reflow():
    # "die" starts the next subtitle, and that one is built normally from there – all limits hold
    text = "Wir haben heute sehr viele Wörter die alle in Untertitel passen müssen und zwar ohne Ende"
    cues = sentence(make_result(text), layout(max_chars=20, max_lines=2))
    assert cues[0][2] == ["Wir haben heute sehr", "viele Wörter"]
    assert cues[1][2][0].startswith("die ")
    assert cues[1][0] == make_result(text)["segments"][0]["words"][6]["start"]     # starts with "die"


def test_two_dangling_words_move_together():
    rules = core.LangRules(lang="de", dangling=frozenset({"mit", "dem"}))
    cues = sentence(make_result("Wir fahren morgen früh mit dem Auto."), layout(max_chars=30, max_lines=1), rules)
    assert texts(cues) == ["Wir fahren morgen früh", "mit dem Auto."]


def test_dangling_word_stays_before_a_long_pause():
    result = make_result("Ich warte auf das", pauses={})
    result["segments"][0]["words"].append({"word": "Ende.", "start": 5.0, "end": 5.3})
    cues = sentence(result, layout(split_pause=1.5))
    assert texts(cues) == ["Ich warte auf das", "Ende."]


@pytest.mark.parametrize("max_chars", [12, 16, 20, 30, 42])
@pytest.mark.parametrize("max_lines", [1, 2])
def test_limits_hold_for_every_layout(max_chars, max_lines):
    text = ("Hier ist der Hund und die Katze die mit dem Ball in den Garten laufen "
            "und dann kommt der Nachbar mit einer großen Leiter über den Zaun")
    cues = sentence(make_result(text), layout(max_chars=max_chars, max_lines=max_lines))
    assert all(len(line) <= max_chars for _, _, lines in cues for line in lines)
    assert all(len(lines) <= max_lines for _, _, lines in cues)
    assert " ".join(" ".join(lines) for _, _, lines in cues) == text      # order kept, no word lost
    assert all(a[1] <= b[0] for a, b in zip(cues, cues[1:]))              # no overlaps


def test_word_with_punctuation_is_never_moved():
    cues = sentence(make_result("Ich nehme das. Und du?"))
    assert texts(cues) == ["Ich nehme das.", "Und du?"]


def test_long_pause_starts_new_subtitle():
    result = make_result("Erst das hier und dann das", pauses={4: 2.0})
    assert texts(sentence(result, layout(split_pause=1.5))) == ["Erst das hier und", "dann das"]
    assert texts(sentence(result, layout(split_pause=0))) == ["Erst das hier und dann das"]


def test_max_duration_limits_display_time():
    text = " ".join(f"Wort{i}" for i in range(30))
    cues = sentence(make_result(text), layout(max_chars=200, max_lines=1, max_duration=3))
    assert len(cues) > 1
    assert all(end - start <= 3 + 1e-9 for start, end, _ in cues)


def test_replacements_words_and_phrases():
    rules = core.LangRules(lang="en",
                           repl_word=core.compile_replacements([("Wisper", "Whisper")]),
                           repl_phrase=core.compile_replacements([("Open AI", "OpenAI")]))
    cues = sentence(make_result("Wisper comes from Open AI."), rules=rules)
    assert texts(cues) == ["Whisper comes from OpenAI."]


def test_replacement_only_whole_words():
    pat = core.compile_replacements([("AI", "KI")])
    assert core.apply_replacements("AI and FAIR", pat) == "KI and FAIR"


# ── Word by word and raw ──────────────────────────────────────────────────────

def test_word_mode_one_subtitle_per_word_without_fillers():
    result = make_result("Hallo ähm Welt")
    cues, mode = core.build_cues(result, "word", DE, layout())
    assert mode == "word"
    assert texts(cues) == ["Hallo", "Welt"]


def test_raw_mode_keeps_segments():
    result = make_result("Ein ganzer Abschnitt so wie er ist")
    cues, mode = core.build_cues(result, "raw", DE, layout())
    assert mode == "raw"
    assert cues == [(0.0, result["segments"][0]["end"], ["Ein ganzer Abschnitt so wie er ist"])]


def test_without_word_times_falls_back_to_raw():
    result = {"segments": [{"start": 1.0, "end": 2.0, "text": "Nur Text"}]}
    cues, mode = core.build_cues(result, "sentence", DE, layout())
    assert mode == "raw"
    assert cues == [(1.0, 2.0, ["Nur Text"])]


def test_srt_format(tmp_path):
    out = tmp_path / "x.srt"
    core.write_srt(out, [(0.0, 1.5, ["Zeile 1", "Zeile 2"]), (3661.25, 3662.0, ["Später"])])
    assert out.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:01,500\nZeile 1\nZeile 2\n\n"
        "2\n01:01:01,250 --> 01:01:02,000\nSpäter\n\n")
