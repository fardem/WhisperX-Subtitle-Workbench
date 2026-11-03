"""Line balancing: words spread evenly over the lines of a subtitle – times unchanged."""

import pytest

import wt_core as core
from helpers import make_result

DE = core.LangRules(lang="de", dangling=frozenset({"der", "die", "das", "in", "mit", "dem"}), ordinal=True)


def build(text, max_chars, max_lines, balance=True, **kw):
    lay = core.Layout(max_chars=max_chars, max_lines=max_lines, balance=balance, **kw)
    return core.cues_sentence(core.collect_words(make_result(text)), DE, lay)


def lines(cues):
    return [lines for _, _, lines in cues]


def test_lonely_last_word_is_avoided():
    text = "müssen und zwar ohne Ende"
    assert lines(build(text, 20, 2, balance=False)) == [["müssen und zwar ohne", "Ende"]]
    assert lines(build(text, 20, 2)) == [["müssen und", "zwar ohne Ende"]]


def test_break_after_comma_is_preferred():
    cues = build("Wenn es morgen regnet, bleiben wir alle gemeinsam zu Hause.", 42, 2)
    assert lines(cues) == [["Wenn es morgen regnet,", "bleiben wir alle gemeinsam zu Hause."]]


def test_no_article_at_the_end_of_a_line():
    cues = build("Am 6. November 2024 haben wir das neue Werkzeug vorgestellt.", 42, 2)
    assert lines(cues) == [["Am 6. November 2024 haben wir", "das neue Werkzeug vorgestellt."]]


def test_three_lines():
    cues = build("Ein sehr langer Satz, der über drei Zeilen geht und dabei in der Mitte bricht.", 30, 3)
    assert lines(cues) == [["Ein sehr langer Satz,", "der über drei Zeilen geht", "und dabei in der Mitte bricht."]]


def test_hyphen_is_no_good_break():
    cues = build("Der Ein- und Ausgang liegt direkt neben dem großen Parkplatz.", 42, 2)
    assert not any(line.endswith("-") for ls in lines(cues) for line in ls)


TEXTS = [
    "Wir haben heute sehr viele Wörter die alle in Untertitel passen müssen und zwar ohne Ende",
    "Hier ist der Hund und die Katze die mit dem Ball in den Garten laufen und dann kommt der Nachbar",
    "Ja, nein, vielleicht, morgen, übermorgen, irgendwann, aber sicher nicht heute in der Nacht.",
    "Bundesverfassungsgericht Donaudampfschifffahrtsgesellschaft und Rindfleischetikettierung",
]


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize("max_chars,max_lines", [(16, 2), (20, 2), (30, 3), (42, 2), (42, 5), (12, 4)])
def test_same_words_same_times_limits_kept(text, max_chars, max_lines):
    off = build(text, max_chars, max_lines, balance=False)
    on = build(text, max_chars, max_lines)
    # same subtitles with the same times and the same words – only the line breaks differ
    assert [(s, e) for s, e, _ in on] == [(s, e) for s, e, _ in off]
    assert [" ".join(ls).split() for ls in lines(on)] == [" ".join(ls).split() for ls in lines(off)]
    assert [len(ls) for ls in lines(on)] == [len(ls) for ls in lines(off)]
    # every line keeps the limit (a single over-long word can never be split)
    assert all(len(line) <= max_chars or " " not in line for ls in lines(on) for line in ls)


def test_off_by_default_for_old_ini(tmp_path):
    ini = tmp_path / "old.ini"
    ini.write_text("[Layout]\ndefault_chars = 42\n", encoding="utf-8")
    assert core.Config(ini).layout().balance is False


def test_on_in_example_ini():
    from helpers import ROOT
    assert core.Config(ROOT / "whisper_tool.example.ini").layout().balance is True


def test_word_and_raw_mode_not_affected():
    result = make_result("müssen und zwar ohne Ende")
    lay = core.Layout(max_chars=20, max_lines=2, balance=True)
    for mode in ("word", "raw"):
        on, _ = core.build_cues(result, mode, DE, lay)
        off, _ = core.build_cues(result, mode, DE, core.Layout(max_chars=20, max_lines=2))
        assert on == off
