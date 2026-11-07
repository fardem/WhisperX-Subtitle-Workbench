"""Which file belongs to which: lyrics and media file, SRTs for the comparison – with letters in either form."""

import unicodedata

import pytest

import wt_core as core
from helpers import ROOT

NAMES = ["Kaçtım", "Şarkı", "Göğsüm", "Üzgün", "İstanbul", "Işık", "Kâğıt", "Mädchen", "Straße"]


def nfd(s):
    return unicodedata.normalize("NFD", s)


def nfc(s):
    return unicodedata.normalize("NFC", s)


@pytest.fixture
def cfg():
    return core.Config(ROOT / "whisper_tool.example.ini")


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("media_form, lyrics_form", [(nfd, nfc), (nfc, nfd), (nfc, nfc)])
def test_lyrics_and_media_find_each_other(tmp_path, cfg, name, media_form, lyrics_form):
    media = tmp_path / media_form(name + ".mp3")
    lyrics = tmp_path / lyrics_form(name + ".lyric.txt")
    media.write_bytes(b"")
    lyrics.write_text("line", encoding="utf-8")
    for dropped in (media, lyrics):
        kind, src, out = core.resolve_input(dropped, cfg)
        assert (kind, src, out.name) == ("lyrics", media, media_form(name) + ".lyric.srt")
    assert core.lyrics_for(media, cfg) == lyrics


@pytest.mark.parametrize("media_name, lyrics_name", [
    ("KAÇTIM.mp3", "Kaçtım.lyric.txt"),
    ("İSTANBUL.mp3", "istanbul.lyric.txt"),
    ("ışık.mp3", "IŞIK.lyric.txt"),
    ("INTRO.mp3", "intro.lyric.txt"),
    ("Song.mp3", "song.LYRIC.TXT"),
])
def test_case_does_not_matter(tmp_path, cfg, media_name, lyrics_name):
    (tmp_path / media_name).write_bytes(b"")
    (tmp_path / nfd(lyrics_name)).write_text("line", encoding="utf-8")
    assert core.resolve_input(tmp_path / nfd(lyrics_name), cfg)[:2] == ("lyrics", tmp_path / media_name)
    assert core.resolve_input(tmp_path / media_name, cfg)[0] == "lyrics"


def test_without_partner(tmp_path, cfg):
    (tmp_path / "Kaçtım.mp3").write_bytes(b"")
    (tmp_path / "Kaçtı.lyric.txt").write_text("line", encoding="utf-8")
    assert core.resolve_input(tmp_path / "Kaçtım.mp3", cfg)[0] == "speech"
    with pytest.raises(core.ToolError):
        core.resolve_input(tmp_path / "Kaçtı.lyric.txt", cfg)


def test_srts_for_the_comparison(tmp_path):
    for name in ("Kaçtım.mp3", nfd("Kaçtım.vocal.srt"), "KAÇTIM.davinci.SRT", "Kaçtım.lyric.txt", "Kaç.srt"):
        (tmp_path / name).write_bytes(b"")
    found = sorted(p.name for p in core.sibling_files(tmp_path / "Kaçtım.mp3", ".srt"))
    assert found == sorted(["KAÇTIM.davinci.SRT", nfd("Kaçtım.vocal.srt")])
