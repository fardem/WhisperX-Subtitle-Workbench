"""Text rules from `CLAUDE.md`: comment limits, no capitals for emphasis, no own version numbers, wording."""

import json
import re
import sys

import pytest

from helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import comments  # noqa: E402
import wt_version  # noqa: E402

LIMITS = json.loads(comments.LIMITS.read_text(encoding="utf-8"))
NOW = comments.current()
LANGS = sorted((ROOT / "lang").glob("*.json"))
READMES = ["README.md", "README.de.md", "README.tr.md"]

# Abbreviations and names written in capitals; any other word in capitals counts as emphasis.
ABBREV = {"AAC", "API", "APPDATA", "ASCII", "BAT", "BOM", "CPU", "CUDA", "DLL", "E402", "EBU", "FFT", "GPU",
          "GUI", "HTML", "IDLE", "INFO", "INI", "IPC", "JSON", "LANG", "MIT", "MMS_FA", "NTSC", "NVIDIA",
          "PATH", "R128", "RTX", "SRT", "VAD", "VLC", "WAV", "WER"}
CAPS = re.compile(r"(?<![\w.-])[A-ZÄÖÜİŞĞÇ][A-ZÄÖÜİŞĞÇ0-9_]{2,}(?![\w-])")
QUOTED = re.compile(r'`[^`]*`|"[^"]*"|“[^”]*”|„[^“]*“')
OWN_VERSION = re.compile(r"\b0\.\d+\.(?:\d+|x)\b")       # the Workbench is 0.x.y; other software is checked below
README_HEAD = re.compile(r"^\*\*\S+ (\d+\.\d+\.\d+)\*\*")

# `CLAUDE.md`, "Begriffe": the program is "the Workbench"; these words were replaced on purpose.
WORDING = {
    "de": [r"\b(?:[Dd]as|dem|ins) Tool\b", r"verschluck", r"kaputt", r"\bHaken\b"],
    "en": [r"\b[Tt]he tool\b", r"swallow"],
    "tr": [r"\b[Aa]raç\b", r"\b[Aa]racı\b", r"\byut"],
}


def lang_texts(path):
    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            yield from o
        else:
            yield o
    return list(walk(json.loads(path.read_text(encoding="utf-8"))))


def capitals(text, allowed=frozenset()):
    return [w for w in CAPS.findall(QUOTED.sub("", text)) if w not in ABBREV and w not in allowed]


@pytest.mark.parametrize("name", sorted(NOW))
def test_comments_stay_within_their_limits(name):
    assert name in LIMITS, "new file: python tools/comments.py --write"
    over = {k: (v, LIMITS[name][k]) for k, v in NOW[name].items() if v > LIMITS[name][k]}
    assert over == {}, "shorten the comments; raise a limit only with a reason in the commit"


def test_limits_name_only_existing_files():
    assert sorted(set(LIMITS) - set(NOW)) == []


@pytest.mark.parametrize("path", comments.files(), ids=lambda p: p.name)
def test_no_capitals_for_emphasis_in_comments(path):
    text, _ = comments.comments(path)
    code = "\n".join(line for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                     if n not in text)
    names = set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", code))     # a constant of the file may be named
    found = {n: capitals(t, names) for n, t in text.items()}
    assert {n: w for n, w in found.items() if w} == {}


@pytest.mark.parametrize("path", LANGS, ids=lambda p: p.stem)
def test_no_capitals_for_emphasis_in_texts(path):
    assert [t for t in lang_texts(path) if capitals(t)] == []


@pytest.mark.parametrize("path", comments.files(), ids=lambda p: p.name)
def test_no_own_version_numbers_in_comments(path):
    text, _ = comments.comments(path)
    assert {n: t for n, t in text.items() if OWN_VERSION.search(t)} == {}


@pytest.mark.parametrize("path", LANGS, ids=lambda p: p.stem)
def test_no_own_version_numbers_in_texts(path):
    assert [t for t in lang_texts(path) if OWN_VERSION.search(t)] == []


def pinned_versions() -> set:
    lines = (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines()
    return {line.split("==")[1].strip() for line in lines if "==" in line and not line.startswith("#")}


@pytest.mark.parametrize("name", READMES)
def test_readme_names_the_own_version_only_in_its_head(name):
    pins = pinned_versions()

    def of_a_package(v):
        return v in pins or (v.endswith(".x") and any(p.startswith(v[:-1]) for p in pins))
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    wrong = [line for line in lines if not README_HEAD.match(line)
             and not all(of_a_package(m.group()) for m in OWN_VERSION.finditer(line))]
    assert wrong == []


@pytest.mark.parametrize("name", READMES)
def test_readme_head_shows_the_current_version(name):
    heads = [m.group(1) for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
             if (m := README_HEAD.match(line))]
    assert heads == [wt_version.VERSION]


@pytest.mark.parametrize("code", sorted(WORDING))
def test_wording(code):
    texts = lang_texts(ROOT / "lang" / f"{code}.json")
    readme = ROOT / ("README.md" if code == "en" else f"README.{code}.md")
    if code != "tr":            # README.tr.md uses "araç" for other tools ("CUDA araç seti")
        texts += readme.read_text(encoding="utf-8").splitlines()
    assert [t for t in texts for w in WORDING[code] if re.search(w, t)] == []
