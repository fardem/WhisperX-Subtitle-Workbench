"""Language files: every language has every text, with the same placeholders."""

import json
import re

import pytest

import wt_i18n as i18n

CODES = [p.stem for p in sorted(i18n.LANG_DIR.glob("*.json"))]
EN = i18n._read("en")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_language_files_are_valid_json():
    for code in CODES:
        json.loads((i18n.LANG_DIR / f"{code}.json").read_text(encoding="utf-8-sig"))
    assert {"en", "de", "tr"} <= set(CODES)


@pytest.mark.parametrize("code", [c for c in CODES if c != "en"])
def test_same_keys_as_english(code):
    other = i18n._read(code)
    assert sorted(set(EN) - set(other)) == [], "missing in " + code
    assert sorted(set(other) - set(EN)) == [], "unknown in " + code


@pytest.mark.parametrize("code", [c for c in CODES if c != "en"])
def test_same_placeholders_as_english(code):
    other = i18n._read(code)
    wrong = {k: (sorted(set(PLACEHOLDER.findall(v))), sorted(set(PLACEHOLDER.findall(other[k]))))
             for k, v in EN.items() if k in other
             and set(PLACEHOLDER.findall(v)) != set(PLACEHOLDER.findall(other[k]))}
    assert wrong == {}


def test_missing_key_falls_back_to_the_key():
    assert i18n.T("does.not.exist") == "does.not.exist"


def test_plural():
    i18n.load("en")
    assert i18n.Tn("core.written", 1, name="x.srt") == "1 subtitle → x.srt"
    assert i18n.Tn("core.written", 3, name="x.srt") == "3 subtitles → x.srt"


def test_unknown_placeholder_stays():
    # a placeholder that is not passed stays visible instead of crashing
    assert i18n.T("core.ffmpeg_fallback", path="x") == "ffmpeg not found: x – using {found}"
