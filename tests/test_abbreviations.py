"""Abbreviations and ordinals stay with the next word: "Dr. Müller", "z. B. gestern", "6. November"."""

import pytest

import wt_core as core
from helpers import ROOT, make_result

CFG = core.Config(ROOT / "whisper_tool.example.ini")


def build(text, lang="de", lay=None):
    return core.cues_sentence(core.collect_words(make_result(text)), CFG.rules(lang), lay or CFG.layout())


def texts(cues):
    return [" / ".join(lines) for _, _, lines in cues]


def test_abbreviations_are_no_sentence_end():
    cues = build("Das hat Dr. Müller z.B. gestern ca. um acht Uhr gesagt.")
    assert len(cues) == 1
    text = " ".join(cues[0][2])
    assert "Dr. Müller" in text and "z.B. gestern" in text and "ca. um" in text


def test_chain_and_multi_part_abbreviation():
    cues = build("Prof. Dr. Müller hat z. B. am 6. November gesprochen. Danach kam Dr. Meier.")
    assert texts(cues) == ["Prof. Dr. Müller hat / z. B. am 6. November gesprochen.", "Danach kam Dr. Meier."]


def test_sentence_end_after_the_name_still_works():
    assert texts(build("Das war Dr. Müller. Danach kam die Pause.")) == ["Das war Dr. Müller.", "Danach kam die Pause."]


def test_unit_has_the_times_of_its_words():
    result = make_result("Dr. Müller")
    words = result["segments"][0]["words"]
    cues = build("Dr. Müller")
    assert cues[0][0] == words[0]["start"] and cues[0][1] == words[1]["end"]


@pytest.mark.parametrize("max_chars", [8, 12, 16, 20, 42])
@pytest.mark.parametrize("max_lines", [1, 2, 3])
@pytest.mark.parametrize("balance", [False, True])
def test_never_split_in_any_layout(max_chars, max_lines, balance):
    text = ("Gestern haben Prof. Dr. Müller und Hr. Meier z. B. am 6. November ca. drei Stunden "
            "mit Fr. Schulz bzw. ihrem Team über Nr. 5 gesprochen.")
    lay = core.Layout(max_chars=max_chars, max_lines=max_lines, balance=balance)
    cues = build(text, lay=lay)
    breaks = [line.split()[-1] for _, _, lines in cues for line in lines]
    assert not any(b in ("Prof.", "Dr.", "Hr.", "z.", "B.", "6.", "ca.", "Fr.", "bzw.", "Nr.") for b in breaks)
    assert " ".join(" ".join(lines) for _, _, lines in cues) == text


def test_english_and_turkish():
    assert texts(build("Mr. Smith met Dr. Jones, e.g. on Monday.", "en")) == ["Mr. Smith met Dr. Jones, e.g. on Monday."]
    assert len(build("Dr. Ayşe Hanım örn. dün konuştu.", "tr")) == 1


def test_word_mode_keeps_single_words():
    cues, mode = core.build_cues(make_result("Dr. Müller kommt."), "word", CFG.rules("de"), CFG.layout())
    assert [lines[0] for _, _, lines in cues] == ["Dr.", "Müller", "kommt."]


def test_missing_line_uses_built_in_list(tmp_path):
    ini = tmp_path / "old.ini"
    ini.write_text("[Layout]\ndefault_chars = 42\n", encoding="utf-8")     # INI of version 0.1.0
    rules = core.Config(ini).rules("de")
    assert ("dr.",) in rules.abbrev and ("z.", "b.") in rules.abbrev


def test_empty_line_switches_off(tmp_path):
    ini = tmp_path / "t.ini"
    ini.write_text("[Abbreviations]\nde =\n", encoding="utf-8")
    cfg = core.Config(ini)
    assert cfg.rules("de").abbrev == ()
    assert ("mr.",) in cfg.rules("en").abbrev                              # other languages keep the default


def test_line_break_inside_an_entry_in_the_ini(tmp_path):
    ini = tmp_path / "t.ini"
    ini.write_text("[Abbreviations]\nde = Dr., z.\n     B., ca.\n", encoding="utf-8")
    assert set(core.Config(ini).rules("de").abbrev) == {("dr.",), ("z.", "b."), ("ca.",)}


def test_template_matches_built_in_list():
    for lang, text in core.ABBREVIATIONS.items():
        assert CFG.rules(lang).abbrev == core.parse_abbreviations(text, lang)


def test_save_and_reload_keeps_multi_part_entries(tmp_path):
    import shutil
    ini = tmp_path / "whisper_tool.ini"
    shutil.copy(ROOT / "whisper_tool.example.ini", ini)
    long = ", ".join(["z. B.", "Dr.", "Prof."] * 12)                      # long enough to be wrapped
    core.save_ini(ini, {("Abbreviations", "de"): long})
    assert set(core.Config(ini).rules("de").abbrev) == {("z.", "b."), ("dr.",), ("prof.",)}
