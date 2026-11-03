"""Transcript comparison: word error rate, verdicts, suggestions, HTML report."""

import pytest

import wt_compare as cmp
from helpers import write_srt

REF = "Das ist ein Test mit WhisperX Modell large"


@pytest.fixture
def files(tmp_path):
    a = write_srt(tmp_path / "a.srt", [(0, 5, "Das ist ein Test mit Whisper Modell large")])
    b = write_srt(tmp_path / "b.srt", [(0, 5, "Das ist ein Tesd mit Wisper Modell large")])
    r = write_srt(tmp_path / "r.srt", [(0, 5, REF)])
    return a, b, r


def test_word_error_rate(files):
    rep = cmp.compare(*files)
    assert rep.err["a"].words == 8
    assert rep.wer_a == pytest.approx(1 / 8)
    assert rep.wer_b == pytest.approx(2 / 8)


def test_suggested_replacements(files):
    rep = cmp.compare(*files)
    pairs = {(wrong, right) for wrong, right, _, _ in rep.suggestions}
    assert pairs == {("Tesd", "Test"), ("Whisper", "WhisperX"), ("Wisper", "WhisperX")}


def test_shared_error_found(tmp_path):
    a = write_srt(tmp_path / "a.srt", [(0, 3, "Hallo Welt von Wisper")])
    b = write_srt(tmp_path / "b.srt", [(0, 3, "Hallo Welt von Wisper")])
    r = write_srt(tmp_path / "r.srt", [(0, 3, "Hallo Welt von Whisper")])
    rep = cmp.compare(a, b, r)
    assert len(rep.shared) == 1
    assert rep.wer_a == rep.wer_b == pytest.approx(1 / 4)


def test_case_and_punctuation_are_no_errors(tmp_path):
    a = write_srt(tmp_path / "a.srt", [(0, 3, "hallo, Welt")])
    r = write_srt(tmp_path / "r.srt", [(0, 3, "Hallo Welt.")])
    rep = cmp.compare(a, path_ref=r)
    assert rep.wer_a == 0
    assert len(rep.format_diffs) == 2


def test_turkish_dotted_i(tmp_path):
    # İ/I ↔ i/ı must compare correctly: "İSTANBUL" and "istanbul" are the same word
    a = write_srt(tmp_path / "a.srt", [(0, 3, "İSTANBUL ışık")])
    r = write_srt(tmp_path / "r.srt", [(0, 3, "istanbul IŞIK")])
    assert cmp.compare(a, path_ref=r).wer_a == 0


def test_needs_two_files(tmp_path):
    a = write_srt(tmp_path / "a.srt", [(0, 3, "Hallo")])
    with pytest.raises(ValueError):
        cmp.compare(a)


def test_html_report(files, tmp_path):
    rep = cmp.compare(*files)
    out = cmp.write_report(rep, tmp_path / "report.html")
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "WhisperX" in html and "Tesd" in html
    from wt_version import VERSION
    assert VERSION in html


def test_summary_line(files):
    assert "12.5" in cmp.summary(cmp.compare(*files))
