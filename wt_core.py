"""
wt_core.py
==========
Core logic for whisper_tool.py (no user interface).

  - settings from whisper_tool.ini, incl. rules per language (de/en/ar/tr)
  - audio via ffmpeg directly into memory (optionally filtered, no temp files)
  - transcription + alignment with WhisperX – models are loaded only once
  - lyrics alignment with MMS_FA when <name>.lyric.txt is next to the file
  - subtitle rules: sentences (orphan, dangling, long sentences), word by word, raw
  - pauses: seamless up to X seconds, a short hold after a real pause (smooth display)
  - checks: gaps (loud passages without text), score (words that fit the audio badly)

Heavy libraries (torch, whisperx, torchaudio, uroman) are only imported
when they are really needed.
"""

import configparser
import gc
import json
import logging
import os
import re
import shutil
import subprocess
import textwrap
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

import wt_i18n as i18n
from wt_i18n import T, Tn

SAMPLE_RATE = 16000

LANGS = ("de", "en", "ar", "tr")                 # languages with their own rules (names: lang/*.json → speechlang)
MODES = ("sentence", "word", "raw")
MEDIA_EXT = (".mp3", ".mp4", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus",
             ".mkv", ".avi", ".mov", ".webm", ".wma")

PUNCT         = ('.', ',', '?', '!', ';', ':', '،', '؛', '؟')
COMMA         = (',', '،')
SENTENCE_END  = ('.', '?', '!', '؟')
ORDINAL       = re.compile(r'\d{1,2}\.')       # "6." in "am 6. November" (German date) is not a sentence end
ORDINAL_LANGS = {"de", "tr"}
STRIP_CHARS   = ".,?!;:،؛؟\"'“”„«»()[]…"

# Abbreviations belong to the next word: never a sentence end, never a line or subtitle
# break between them ("Dr. Müller", "z. B. gestern"). Used when a language has no entry
# in [Abbreviations] of the INI. Not included on purpose: usw., etc. – they often end a sentence.
ABBREVIATIONS = {
    "de": "Dr., Prof., Dipl.-Ing., Hr., Fr., Nr., St., z. B., z.B., d. h., d.h., u. a., u.a., "
          "bzw., ca., ggf., evtl., vgl., inkl., bspw., sog.",
    "en": "Mr., Mrs., Ms., Dr., Prof., e.g., i.e., vs., approx.",
    "tr": "Dr., Prof., Doç., Av., Sn., Yrd., örn., No.",
    "ar": "د.",
}

# No console window for ffmpeg when the tool runs without a console (pythonw)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):
    pass


class ToolError(RuntimeError):
    """Expected error with an understandable message (no traceback needed)."""


# ─── TEXT HELPERS ─────────────────────────────────────────────────────────────

def abbr_key(token: str, lang: str) -> str:
    """Word for comparing with the abbreviation list: lower case, the dot stays."""
    t = token.strip().lstrip("(\"'„“«[¿¡")
    if lang == "tr":
        t = t.replace("I", "ı").replace("İ", "i")
    return t.lower()


def parse_abbreviations(text: str, lang: str) -> tuple:
    """ "Dr., z. B." → (("dr.",), ("z.", "b.")) – longest first, so "z. B." wins over "B." """
    # only the comma separates entries – a line break in the INI may fall between "z." and "B."
    entries = {tuple(abbr_key(t, lang) for t in e.split()) for e in text.replace("\n", " ").split(",") if e.strip()}
    return tuple(sorted(entries, key=len, reverse=True))


def norm(text: str, lang: str) -> str:
    """Word for comparisons: punctuation removed, lower case (Turkish: I→ı, İ→i)."""
    t = text.strip().strip(STRIP_CHARS)
    if lang == "tr":
        t = t.replace("I", "ı").replace("İ", "i")
    return t.lower()


def compile_replacements(items) -> list:
    """Replace whole words/phrases only, case-insensitive, longest first."""
    patterns = []
    for wrong, right in sorted(items, key=lambda kv: -len(kv[0])):
        wrong = wrong.strip()
        if wrong:
            pat = re.compile(r'(?<!\w)' + re.escape(wrong) + r'(?!\w)', re.IGNORECASE)
            patterns.append((pat, right.strip()))
    return patterns


def apply_replacements(text: str, patterns) -> str:
    for pat, right in patterns:
        text = pat.sub(lambda _m, r=right: r, text)
    return text


def fmt_time(seconds: float) -> str:
    ms = int(round(max(seconds, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# ─── CONFIGURATION ────────────────────────────────────────────────────────────

@dataclass
class LangRules:
    lang:        str
    prompt:      str       = ""
    fillers:     frozenset = frozenset()
    dangling:    frozenset = frozenset()
    repl_word:   list      = field(default_factory=list)
    repl_phrase: list      = field(default_factory=list)
    ordinal:     bool      = False
    abbrev:      tuple     = ()        # (("dr.",), ("z.", "b.")) – see parse_abbreviations

    @property
    def repl_all(self) -> list:
        return self.repl_phrase + self.repl_word


@dataclass
class Layout:
    max_chars:    int   = 45
    max_lines:    int   = 1
    orphan_limit: int   = 4
    orphan_fill:  int   = 80
    pause:        float = 0.0       # a speech pause longer than this is a real pause → new subtitle
    max_duration: float = 0.0       # split long sentences: at most this many seconds of speech
    balance:      bool  = False     # sentence mode: distribute the words evenly over the lines


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "ja", "yes", "on")


class Config:
    """Reads whisper_tool.ini. Missing/broken values → default + warning."""

    def __init__(self, path):
        self.path     = Path(path)
        self.warnings: list[str] = []
        self._cp      = configparser.ConfigParser(delimiters=('=',), interpolation=None, strict=False)
        try:
            if not self._cp.read(self.path, encoding='utf-8-sig'):
                self.warnings.append(T("core.ini_missing", path=self.path))
        except configparser.Error as e:
            self.warnings.append(T("core.ini_broken", err=e.__class__.__name__))
            self._cp = configparser.ConfigParser()
        self._rules: dict[str, LangRules] = {}

        g = self._get
        # System
        self.ffmpeg            = self._find_ffmpeg(g("System", "ffmpeg_path", "ffmpeg"))
        self.device            = g("System", "device", "cuda").lower()
        # Menu / defaults
        self.menu_timeout      = g("Menu", "timeout", 15, int)
        self.menu_skip         = g("Menu", "skip", False, _bool)
        self.default_filter    = g("Menu", "ffmpeg", True, _bool)
        self.default_language  = g("Menu", "language", "auto").lower()
        self.default_mode      = g("Menu", "mode", "sentence").lower()
        self.prompt_mode       = g("Menu", "prompt_mode", "ini").lower()
        self.custom_prompt     = " ".join(g("Menu", "custom_prompt", "").split())
        self.live_lines        = g("Menu", "live_lines", True, _bool)
        # Whisper
        self.model             = g("Whisper", "model", "large-v3")
        self.models            = {k[6:]: v.strip() for k, v in self._section("Whisper").items()
                                  if k.startswith("model.") and v.strip()}
        self.beam_size         = g("Whisper", "beam_size", 5, int)
        self.compute_type      = g("Whisper", "compute_type", "float16")
        self.batch_size        = g("Whisper", "batch_size", 8, int)
        self.suppress_numerals = g("Whisper", "suppress_numerals", False, _bool)
        # VAD
        self.vad_method        = g("VAD", "method", "pyannote").lower()
        self.vad_onset         = g("VAD", "onset", 0.5, float)
        self.vad_offset        = g("VAD", "offset", 0.363, float)
        self.chunk_size        = g("VAD", "chunk_size", 30, int)
        # Audio
        self.filter_string     = " ".join(g("Audio", "filter_string", "").split())
        # Output
        self.keep_json         = g("Output", "keep_json", False, _bool)
        self.speech_suffix     = g("Output", "speech_suffix", ".srt")
        self.lyrics_input      = g("Output", "lyrics_input", ".lyric.txt")
        self.lyrics_suffix     = g("Output", "lyrics_suffix", ".lyric.srt")
        self.encoding          = g("Output", "encoding", "utf-8")
        # Layout
        self.default_chars     = g("Layout", "default_chars", 45, int)
        self.default_lines     = g("Layout", "default_lines", 1, int)
        self.orphan_limit      = g("Layout", "orphan_limit", 4, int)
        self.orphan_fill       = g("Layout", "orphan_min_fill", 80, int)
        self.max_duration      = g("Layout", "max_duration", 0.0, float)
        self.balance_lines     = g("Layout", "balance_lines", False, _bool)
        # Pauses (speech): a pause longer than "pause" is a real pause
        self.pause             = g("Layout", "pause", 2.0, float)
        self.smooth            = g("Layout", "smooth", True, _bool)
        self.pause_hold        = g("Layout", "pause_hold", 1.0, float)
        # Lyrics
        self.line_hold         = g("Lyrics", "line_hold", 0.0, float)
        self.line_gap          = g("Lyrics", "line_gap", 0.05, float)
        # Gap check
        self.gap_check         = g("Gaps", "enabled", True, _bool)
        self.gap_min           = g("Gaps", "min_length", 1.0, float)
        self.gap_margin        = g("Gaps", "margin_db", 8.0, float)
        self.gap_srt           = g("Gaps", "write_srt", True, _bool)
        # Score check: words below the threshold fit the audio badly (0 = off – also without [Score])
        self.score_threshold   = g("Score", "threshold", 0.0, float)
        self.score_srt         = g("Score", "write_srt", True, _bool)
        # DaVinci Resolve: own countdown for jobs of the plugin (0 = off), hand over by itself,
        # gaps and fast subtitles as own tracks, close instead of minimize.
        # track_gaps / track_fast replace extra_tracks (0.6.x) – a missing key takes its value
        self.resolve_countdown = max(g("Resolve", "countdown", 10, int), 0)
        self.resolve_auto      = g("Resolve", "auto_hand_over", False, _bool)
        extra                  = g("Resolve", "extra_tracks", True, _bool)
        self.resolve_gaps      = g("Resolve", "track_gaps", extra, _bool)
        self.resolve_fast      = g("Resolve", "track_fast", extra, _bool)
        self.resolve_close     = g("Resolve", "close_after", False, _bool)

        if self.score_threshold != 0 and not SCORE_MIN <= self.score_threshold <= SCORE_MAX:
            self.warnings.append(T("core.ini_invalid", entry=f"[Score] threshold={self.score_threshold:g}",
                                   default="0"))
            self.score_threshold = 0.0
        if self.smooth and self.pause_hold >= self.pause:      # a real pause must stay visible
            hold = round(self.pause / 2, 2)
            self.warnings.append(T("core.ini_invalid", entry=f"[Layout] pause_hold={self.pause_hold:g} (≥ pause)",
                                   default=f"{hold:g}"))
            self.pause_hold = hold
        if self.default_language != "auto" and self.default_language not in LANGS:
            self.warnings.append(T("core.ini_unknown", entry=f"[Menu] language={self.default_language!r}", default="auto"))
            self.default_language = "auto"
        if self.default_mode not in MODES:
            self.warnings.append(T("core.ini_unknown", entry=f"[Menu] mode={self.default_mode!r}", default="sentence"))
            self.default_mode = "sentence"
        if self.prompt_mode not in ("ini", "custom", "none"):
            self.warnings.append(T("core.ini_unknown", entry=f"[Menu] prompt_mode={self.prompt_mode!r}", default="ini"))
            self.prompt_mode = "ini"

    def _section(self, name) -> dict:
        return dict(self._cp[name]) if self._cp.has_section(name) else {}

    def raw(self, section, key, default="") -> str:
        """Value as written in the INI (for the settings dialog)."""
        return self._cp.get(section, key).strip() if self._cp.has_option(section, key) else default

    def _get(self, section, key, default, cast=str):
        if not self._cp.has_option(section, key):
            return default
        raw = self._cp.get(section, key)
        try:
            return cast(raw) if cast is not str else raw.strip()
        except ValueError:
            self.warnings.append(T("core.ini_invalid", entry=f"[{section}] {key}={raw!r}", default=default))
            return default

    def _find_ffmpeg(self, configured: str) -> str:
        if Path(configured).is_file() or shutil.which(configured):
            return configured
        fallback = shutil.which("ffmpeg")
        self.warnings.append(T("core.ffmpeg_fallback", path=configured, found=fallback) if fallback
                             else T("core.ffmpeg_check", path=configured))
        return fallback or configured

    def model_signature(self) -> tuple:
        """If this changes, loaded Whisper models must be reloaded."""
        return (self.device, self.compute_type, self.beam_size, self.suppress_numerals,
                self.vad_method, self.vad_onset, self.vad_offset, self.chunk_size)

    def layout(self, max_chars=None, max_lines=None) -> Layout:
        return Layout(max_chars or self.default_chars, max_lines or self.default_lines,
                      self.orphan_limit, self.orphan_fill, self.pause, self.max_duration,
                      balance=self.balance_lines)

    def rules(self, lang: str) -> LangRules:
        if lang not in self._rules:
            def words(section):
                raw = self._section(section).get(lang, "")
                return frozenset(norm(w, lang) for w in raw.split(",") if w.strip())
            repl = {**self._section("Replacements"), **self._section(f"Replacements.{lang}")}
            abbrev = self._section("Abbreviations")      # a language without an entry gets the built-in list
            self._rules[lang] = LangRules(
                lang        = lang,
                prompt      = " ".join(self._section("Prompt").get(lang, "").split()),
                fillers     = words("Filler"),
                dangling    = words("Dangling"),
                repl_word   = compile_replacements([(k, v) for k, v in repl.items() if ' ' not in k.strip()]),
                repl_phrase = compile_replacements([(k, v) for k, v in repl.items() if ' ' in k.strip()]),
                ordinal     = lang in ORDINAL_LANGS,
                abbrev      = parse_abbreviations(abbrev[lang] if lang in abbrev else ABBREVIATIONS.get(lang, ""), lang),
            )
        return self._rules[lang]


# ─── AUDIO ────────────────────────────────────────────────────────────────────

def load_audio(path, ffmpeg: str, audio_filter: str | None = None) -> np.ndarray:
    """Any audio/video file → 16 kHz mono float32, directly in memory."""
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-vn"]
    if audio_filter:
        cmd += ["-af", audio_filter]
    cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise ToolError(T("core.ffmpeg_missing", path=ffmpeg)) from None
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ToolError("ffmpeg: " + (err[-1] if err else T("core.exit_code", code=proc.returncode)))
    audio = np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        raise ToolError(T("core.no_audio"))
    return audio


# ─── SUBTITLE RULES ───────────────────────────────────────────────────────────
# Cue = (start, end, [line1, line2, ...])

def fill_missing_times(words: list[dict], seg_start: float, seg_end: float) -> list[dict]:
    """
    WhisperX returns no timestamps for some words (especially numbers like "6."
    or "2024"). Instead of dropping them, derive the time from the neighbouring
    words in the same segment.
    """
    words = [dict(w) for w in words]
    for i, w in enumerate(words):
        if 'start' in w and 'end' in w:
            continue
        prev_end   = next((words[j]['end']   for j in range(i - 1, -1, -1)  if 'end'   in words[j]), seg_start)
        next_start = next((words[j]['start'] for j in range(i + 1, len(words)) if 'start' in words[j]), seg_end)
        w['start'] = prev_end
        w['end']   = max(prev_end, next_start)
    return words


def collect_words(result: dict) -> list[dict]:
    words = []
    for seg in result.get('segments', []):
        words.extend(fill_missing_times(seg.get('words', []), seg.get('start', 0.0), seg.get('end', 0.0)))
    return words


def cues_raw(result: dict, rules: LangRules, pause: float = 0.0) -> list:
    """
    WhisperX segments unchanged (1 segment = 1 cue) – except at a real pause (longer than
    `pause` seconds) inside a segment: there it is split, so the text does not stay on screen
    during applause or music. Needs word times; a segment without them stays as it is.
    """
    cues = []
    for seg in result.get('segments', []):
        text = seg.get('text', '').strip()
        if not text:
            continue
        start, end = seg.get('start', 0), seg.get('end', 0)
        words = [w for w in fill_missing_times(seg.get('words', []), start, end) if w.get('word', '').strip()]
        cuts = [i for i in range(1, len(words)) if pause > 0 and words[i]['start'] - words[i - 1]['end'] > pause]
        if not cuts:
            cues.append((start, end, [apply_replacements(text, rules.repl_all)]))
            continue
        for k, (a, b) in enumerate(zip([0] + cuts, cuts + [len(words)])):
            part = " ".join(w['word'].strip() for w in words[a:b])
            cues.append((start if k == 0 else words[a]['start'],
                         end if b == len(words) else words[b - 1]['end'],
                         [apply_replacements(part, rules.repl_all)]))
    return cues


def cues_word(words: list[dict], rules: LangRules) -> list:
    """Word by word, fillers removed, replacements applied."""
    cues = []
    for w in words:
        text = apply_replacements(w['word'].strip(), rules.repl_word)
        if norm(text, rules.lang) not in rules.fillers:
            cues.append((w['start'], w['end'], [text]))
    return cues


LINE_BREAK_AFTER = (',', ';', ':', '،', '؛', '…') + SENTENCE_END   # not '-': "Ein- / und Ausgang"


def balance_lines(words: list[dict], n_lines: int, max_chars: int, is_dangling) -> list[list[dict]]:
    """
    Distribute the words of ONE subtitle evenly over n_lines lines – instead of filling
    the first line to the limit and leaving a lonely word in the last one:
        "müssen und zwar ohne / Ende"  →  "müssen und / zwar ohne Ende"
    Same words, same order, same number of lines – so the times of the subtitle stay
    exactly the same. Every line keeps max_chars. Preferred, in this order:
      - no article/preposition at the end of a line ("das / neue Haus" is fine,
        "das neue / Haus" too, but not "… in / der Stadt")
      - a break after a comma or sentence mark
      - lines of similar length, the lower one rather longer than the upper one
    """
    n = len(words)
    if n_lines < 2:
        return [words]
    if n <= n_lines:
        return [[w] for w in words]
    lens = [len(w['word']) for w in words]
    pre = [0]
    for x in lens:
        pre.append(pre[-1] + x)

    def width(a, b):                          # characters of words[a:b] incl. spaces
        return pre[b] - pre[a] + (b - a - 1)

    def pair(upper, lower):                   # even, and rather bottom-heavy than top-heavy
        return abs(upper - lower) + 0.5 * max(0, upper - lower)

    def cut(i):                               # line break before words[i]
        last = words[i - 1]
        if is_dangling(last):
            return max_chars * 0.5            # "… in / der Stadt"
        if last['word'].endswith(LINE_BREAK_AFTER):
            return -max_chars * 0.4           # natural break after a comma – worth a less even split
        return 0.0

    # dp[(k, i, j)]: best cost for words[:j] in k lines, the last line being words[i:j]
    dp, back = {}, {}
    for j in range(1, n - n_lines + 2):
        if width(0, j) <= max_chars:
            dp[(1, 0, j)] = 0.0
    for k in range(2, n_lines + 1):
        for (kk, h, i), c in [item for item in dp.items() if item[0][0] == k - 1]:
            for j in range(i + 1, n - (n_lines - k) + 1):
                w_ = width(i, j)
                if w_ > max_chars:
                    break
                cost = c + pair(width(h, i), w_) + cut(i)
                key = (k, i, j)
                if key not in dp or cost < dp[key]:
                    dp[key], back[key] = cost, h
    ends = [(c, key) for key, c in dp.items() if key[0] == n_lines and key[2] == n]
    if not ends:                              # cannot happen with greedy input – keep it as it is
        return wrap_greedy(words, max_chars)
    _, (k, i, j) = min(ends)
    bounds = [n]
    while k > 1:
        bounds.append(i)
        i, j, k = back[(k, i, j)], i, k - 1
    bounds.append(0)
    bounds.reverse()
    return [words[a:b] for a, b in zip(bounds, bounds[1:])]


def wrap_greedy(words: list[dict], max_chars: int) -> list[list[dict]]:
    lines, line, used = [], [], 0
    for w in words:
        if line and used + len(w['word']) > max_chars:
            lines.append(line)
            line, used = [], 0
        line.append(w)
        used += len(w['word']) + 1
    return lines + [line] if line else lines


def glue_words(words: list[dict], rules: LangRules) -> list[dict]:
    """
    Abbreviations ("Dr.", "z. B.") and – in German/Turkish – ordinal numbers ("6.") belong
    to the next word. They are merged into ONE unit ("Dr. Müller", "6. November") with the
    start of the first and the end of the last word, so no rule can break between them:
    no line break, no new subtitle, no sentence end. Chains work too ("Prof. Dr. Müller").
    """
    n = len(words)
    keys = [abbr_key(w['word'], rules.lang) for w in words]
    glue = [False] * n                        # glue[i]: word i stays with word i + 1
    i = 0
    while i < n:
        size = next((len(ab) for ab in rules.abbrev if tuple(keys[i:i + len(ab)]) == ab), 0)
        if size:
            glue[i:i + size] = [True] * size
            i += size
            continue
        if rules.ordinal and ORDINAL.fullmatch(words[i]['word']):
            glue[i] = True
        i += 1
    out: list[dict] = []
    for i, w in enumerate(words):
        if out and glue[i - 1]:
            prev = out[-1]
            out[-1] = {'word': f"{prev['word']} {w['word']}", 'start': prev['start'], 'end': w['end']}
        else:
            out.append({'word': w['word'], 'start': w['start'], 'end': w['end']})
    return out


def cues_sentence(words: list[dict], rules: LangRules, layout: Layout) -> list:
    """
    Smart split with:
    - filler filter
    - replacements (single words per word, phrases on the finished line)
    - orphan rule (short words after a comma move to the next segment)
    - dangling word rule (article/preposition at the end of a full subtitle → starts the next one)
    - pause rule (optional)
    - line balancing (optional): even lines inside a subtitle, times unchanged
    - abbreviations and ordinals stay with the next word ("Dr. Müller", "6. November")
    """
    lang = rules.lang

    def is_sentence_end(t):
        return t.endswith(SENTENCE_END) and not (rules.ordinal and ORDINAL.fullmatch(t))

    # Build a clean word list
    clean = []
    for w in words:
        t = apply_replacements(w['word'].strip(), rules.repl_word)
        if not t:
            continue
        if norm(t, lang) in rules.fillers:
            # pass the sentence end of a filler ("uh.") on to the previous word
            if t.endswith(SENTENCE_END) and clean and not clean[-1]['word'].endswith(PUNCT):
                clean[-1]['word'] += t[-1]
            continue
        clean.append({'word': t, 'start': w['start'], 'end': w['end']})

    if not clean:
        return []
    clean = glue_words(clean, rules)          # "Dr. Müller" is one unit from here on

    def is_dangling(w):
        last = w['word'].split()[-1]          # of a unit like "z. B. am" the last word counts
        return not last.endswith(PUNCT) and norm(last, lang) in rules.dangling

    # ── Build segments ─────────────────────────────────────────────────────────
    # The subtitles are built from the words alone (WhisperX segments do not matter).
    segments: list[list[list[dict]]] = []
    block:    list[list[dict]] = []
    line:     list[dict]       = []
    line_len  = 0                       # characters incl. one space per word
    carried   = 0                       # words at the start of the block that came from the previous one
    last_text = ""
    last_end  = 0.0

    def add(w):
        nonlocal line_len
        line.append(w)
        line_len += len(w['word']) + 1

    def flush_line():
        nonlocal line, line_len
        if line:
            block.append(line)
        line, line_len = [], 0

    def flush_block(next_word=None):
        """
        Close the subtitle. With next_word (block closed because it is full or too long):
        dangling word rule – articles/prepositions at the end ("… the") start the next
        subtitle instead. Then the next subtitle is built normally from them, so every
        following subtitle keeps all limits.
        """
        nonlocal block, carried
        flush_line()
        tail: list[dict] = []
        if next_word is not None:
            flat = [w for ln in block for w in ln]
            k = len(flat)
            # keep at least one own word – never move the words carried over before (no loop)
            while k - 1 >= carried + 1 and is_dangling(flat[k - 1]):
                k -= 1
            tail = flat[k:]
            # only if they fit together with the next word on one line
            if sum(len(w['word']) + 1 for w in tail) + len(next_word['word']) > layout.max_chars:
                tail = []
            for _ in tail:
                block[-1].pop()
                if not block[-1]:
                    block.pop()
        if block:
            segments.append(block)
        block = []
        for w in tail:
            add(w)
        carried = len(tail)

    for w in clean:
        t = w['word']

        # pause rule: a real pause (longer than "pause") → close the block (a word before it stays)
        if block or line:
            block_start = (block[0][0] if block else line[0])['start']
            if layout.pause > 0 and w['start'] - last_end > layout.pause:
                flush_block()
            elif layout.max_duration > 0 and w['end'] - block_start > layout.max_duration:
                flush_block(w)

        # orphan rule: short word after a comma → close the block
        if line and len(t) <= layout.orphan_limit and last_text.endswith(COMMA):
            if line_len > layout.max_chars * (layout.orphan_fill / 100.0):
                flush_block()

        # line break at the character limit
        if line and (line_len + len(t)) > layout.max_chars:
            flush_line()
            if len(block) >= layout.max_lines:
                flush_block(w)

        add(w)
        last_text = t
        last_end  = w['end']

        if is_sentence_end(t):
            flush_block()

    flush_block()

    # ── Line balancing ─────────────────────────────────────────────────────────
    if layout.balance:
        segments = [balance_lines([w for ln in seg for w in ln], len(seg), layout.max_chars, is_dangling)
                    if len(seg) > 1 else seg for seg in segments]

    # ── Cues ───────────────────────────────────────────────────────────────────
    cues = []
    for seg in segments:
        seg_words = [w for ln in seg for w in ln]
        lines = [apply_replacements(" ".join(w['word'] for w in ln), rules.repl_phrase) for ln in seg]
        cues.append((seg_words[0]['start'], seg_words[-1]['end'], lines))
    return cues


# ─── PAUSES (speech) ──────────────────────────────────────────────────────────
# One rule, only the length of the pause counts – inside a sentence or between two sentences:
#   pause up to X seconds      → the segment stays until the next one starts (seamless)
#   pause longer than X        → a real pause: the segment stays "hold" seconds, then the screen is empty
# Lyrics keep their own rules ("hold line", gap to the next line).

READING_CPS = 17        # characters per second – faster subtitles are listed in the log (sentence mode)


def cue_chars(lines) -> int:
    """Characters a viewer has to read (spaces and punctuation count, line breaks do not)."""
    return sum(len(line) for line in lines)


def smooth_cues(cues: list, pause: float, hold: float, duration: float | None = None) -> list:
    """
    Flicker-free display, e.g. for the subtitle track of DaVinci Resolve:
      - pause to the next segment at most `pause` seconds → the end becomes exactly the start of
        the next one, so the screen is never empty for a moment (overlaps are cut the same way)
      - longer pause (a real pause) → the segment stays `hold` seconds more, then the screen is
        empty until the next one – never into the next segment, never beyond the end of the audio
    Starts never move: every word and sentence still appears exactly when it is spoken.
    """
    if not cues:
        return cues
    out = []
    for i, (start, end, lines) in enumerate(cues):
        if i + 1 < len(cues):
            nxt = cues[i + 1][0]
            if nxt - end <= pause:                    # short pause (or overlap: negative)
                if nxt > start:
                    end = nxt
            else:                                      # real pause
                end = min(end + hold, nxt)
        else:
            end = end + hold if duration is None else max(end, min(end + hold, duration))
        out.append((start, end, lines))
    return out


def display_cues(cues: list, mode: str, cfg, duration: float | None = None) -> list:
    """Display rule for speech (sentences, word by word, raw) if "smooth" is on – lyrics keep their own."""
    if mode == "lyrics" or not cfg.smooth:
        return cues
    return smooth_cues(cues, cfg.pause, cfg.pause_hold, duration)


def fast_cues(cues: list, max_cps: float = READING_CPS) -> list[tuple[float, float, float]]:
    """→ [(start, end, characters_per_second)] for subtitles that are too fast to read."""
    fast = []
    for start, end, lines in cues:
        cps = cue_chars(lines) / max(end - start, 0.001)
        if cps > max_cps + 0.05:            # tolerance for rounding to milliseconds
            fast.append((start, end, cps))
    return fast


def build_cues(result: dict, mode: str, rules: LangRules, layout: Layout) -> tuple[list, str]:
    """Returns (cues, actual_mode) – without word timestamps it falls back to raw.
    The times are the spoken ones; display_cues then applies the pause rule."""
    if mode == "raw":
        return cues_raw(result, rules, layout.pause), "raw"
    words = collect_words(result)
    if not words:
        return cues_raw(result, rules, layout.pause), "raw"
    if mode == "word":
        return cues_word(words, rules), "word"
    return cues_sentence(words, rules, layout), "sentence"


def write_srt(path, cues, encoding="utf-8"):
    text = "".join(f"{n}\n{fmt_time(start)} --> {fmt_time(end)}\n" + "\n".join(lines) + "\n\n"
                   for n, (start, end, lines) in enumerate(cues, start=1))
    Path(path).write_text(text, encoding=encoding)


# ─── GAP CHECK ────────────────────────────────────────────────────────────────
# Finds passages that are loud and voice-like but got no text –
# e.g. because the VAD swallowed quiet speech or Whisper skipped a sentence.

GAP_FRAME     = 0.05     # analysis window in seconds (800 samples, 20 Hz per FFT bin)
GAP_TONAL     = 0.35     # spectral flatness below this = fundamental/overtones (voice, music)
GAP_TONAL_MIN = 0.30     # share of "tonal" windows from which a loud passage counts as voice-like


def _frame_features(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Level (dBFS) and spectral flatness (100–4000 Hz) per 50 ms window."""
    n = int(SAMPLE_RATE * GAP_FRAME)
    count = len(audio) // n
    if count == 0:
        return np.zeros(0), np.zeros(0)
    x = audio[:count * n].reshape(count, n)
    db = 20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2, axis=1)) + 1e-9)
    win = np.hanning(n).astype(np.float32)
    lo, hi = int(100 * GAP_FRAME), int(4000 * GAP_FRAME)      # bin = frequency * window length
    flat = np.empty(count, np.float32)
    for i in range(0, count, 4096):                           # in blocks – saves memory with long files
        p = np.abs(np.fft.rfft(x[i:i + 4096] * win, axis=1))[:, lo:hi] ** 2 + 1e-12
        flat[i:i + 4096] = np.exp(np.mean(np.log(p), axis=1)) / np.mean(p, axis=1)
    return db, flat


def find_gaps(audio: np.ndarray, result: dict, min_len: float = 1.0, margin_db: float = 8.0) -> list[tuple]:
    """→ [(start, end, level_relative_to_speech_db)] for loud, voice-like passages without text."""
    db, flat = _frame_features(audio)
    if db.size == 0:
        return []
    covered = np.zeros(db.size, bool)
    spans = [(w["start"], w["end"]) for w in collect_words(result)] \
        or [(s.get("start", 0), s.get("end", 0)) for s in result.get("segments", [])]
    for s, e in spans:                                        # 0.25 s margin around every word
        covered[max(int((s - 0.25) / GAP_FRAME), 0):min(int((e + 0.25) / GAP_FRAME) + 1, db.size)] = True

    audible = db > -80                                        # ignore digital silence
    if not audible.any():
        return []
    speech = covered & audible
    speech_ref = float(np.median(db[speech])) if speech.sum() > 20 else float(np.percentile(db[audible], 90))
    floor = max(float(np.percentile(db[audible], 10)), speech_ref - 40)
    loud = (db > floor + margin_db) & ~covered

    # connected loud areas, bridge short dropouts (< 0.3 s)
    runs, i, bridge = [], 0, int(0.3 / GAP_FRAME)
    idx = np.flatnonzero(loud)
    for k in idx:
        if runs and k - runs[-1][1] <= bridge and not covered[runs[-1][1]:k].any():
            runs[-1][1] = k + 1
        else:
            runs.append([k, k + 1])

    gaps = []
    for a, b in runs:
        if (b - a) * GAP_FRAME < min_len:
            continue
        lf = loud[a:b]
        tonal = ((flat[a:b] < GAP_TONAL) & lf).sum() / max(lf.sum(), 1)
        if tonal >= GAP_TONAL_MIN:
            gaps.append((a * GAP_FRAME, b * GAP_FRAME, float(np.median(db[a:b][lf])) - speech_ref))
    return gaps


def fill_suffix(suffix: str, model: str | None, lang: str | None) -> str:
    """Placeholders in the file suffix: {model} → model name (last part for folders/repos), {lang} → language."""
    tag = re.split(r"[\\/]", (model or "").rstrip("\\/"))[-1] or "modell"
    tag = re.sub(r'[<>:"/\\|?*\s]+', "_", tag)
    return suffix.replace("{model}", tag).replace("{lang}", lang or "xx")


def fmt_pos(sec: float) -> str:
    """Time position for messages: m:ss.d or h:mm:ss.d"""
    m, s = divmod(sec, 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


def write_gap_srt(path, gaps, encoding="utf-8"):
    write_srt(path, [(s, e, [T("core.gap_cue", len=i18n.secs(e - s), db=f"{rel:+.0f}")])
                     for s, e, rel in gaps], encoding)


# ─── SCORE CHECK ──────────────────────────────────────────────────────────────
# whisperx.align() gives every word a "score" (0–1): the mean of its letters' scores, every
# letter the mean frame probability on the alignment path. A low score means the word fits
# the audio badly at this place – it was misrecognized or misplaced. Read only, never changed:
# the subtitles stay exactly the same.

SCORE_MIN, SCORE_MAX = 0.05, 0.95  # allowed thresholds ([Score] threshold; 0 = off)
SCORE_LETTERS = 3                  # words with fewer letters are not rated ("in", "z.B.")
SCORE_JOIN    = 1.0                # flagged words at most this far apart (s) become one entry
SCORE_MIN_LEN = 0.5                # an entry lasts at least this long (s) – never into the next one
SCORE_WORDS   = 4                  # words shown per entry, the rest as "+n"


def word_score(w: dict) -> float | None:
    """Score of a rated word – None without a score or with fewer than 3 letters."""
    if sum(c.isalpha() for c in str(w.get("word", ""))) < SCORE_LETTERS:
        return None
    try:
        s = float(w["score"])
    except (KeyError, TypeError, ValueError):
        return None
    return s if np.isfinite(s) else None


def _rated_words(result: dict) -> list[tuple[dict, float]]:
    return [(w, s) for seg in result.get("segments", []) for w in seg.get("words", [])
            if (s := word_score(w)) is not None]


def score_stats(result: dict) -> tuple[float, float] | None:
    """(median, 5 % quantile) of all rated words – None if there is no rated word."""
    scores = [s for _, s in _rated_words(result)]
    if not scores:
        return None
    return float(np.median(scores)), float(np.percentile(scores, 5))


def find_unsure(result: dict, threshold: float) -> list[tuple[float, float, list[tuple[str, float]]]]:
    """
    → [(start, end, [(word, score), …])] for rated words with a score below `threshold`.
    Flagged words at most SCORE_JOIN s apart (start of the next minus end of the previous)
    become one entry. It starts exactly at the first word and ends at the end of the last one,
    but lasts at least SCORE_MIN_LEN s – never beyond the start of the next entry.
    """
    flagged = []
    for w, s in _rated_words(result):
        if s < threshold and "start" in w and "end" in w:
            word = str(w["word"]).strip()
            flagged.append((float(w["start"]), float(w["end"]), word.strip(STRIP_CHARS) or word, s))
    flagged.sort(key=lambda x: x[0])
    groups: list[list] = []                  # [start, end, words]
    for start, end, word, s in flagged:
        if groups and start - groups[-1][1] <= SCORE_JOIN + 1e-6:
            groups[-1][1] = max(groups[-1][1], end)
            groups[-1][2].append((word, s))
        else:
            groups.append([start, end, [(word, s)]])
    out = []
    for i, (start, end, words) in enumerate(groups):
        end = max(end, start + SCORE_MIN_LEN)
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0])
        out.append((start, end, words))
    return out


def unsure_words(words, limit: int = SCORE_WORDS) -> str:
    """[(word, score)] → "Rafah 0.28 · Gaza 0.31 +2" – at most `limit` words, decimal sign per language."""
    text = " · ".join(f"{w} {i18n.num(s, 2)}" for w, s in words[:limit])
    return text + (f" +{len(words) - limit}" if len(words) > limit else "")


def unsure_label(words) -> str:
    """Text of an entry, e.g. "unsure: Rafah 0.28 · Gaza 0.31" (score file, track in DaVinci Resolve)."""
    return T("core.score_cue", words=unsure_words(words))


def write_score_srt(path, unsure, encoding="utf-8"):
    write_srt(path, [(s, e, [unsure_label(words)]) for s, e, words in unsure], encoding)


# ─── LYRICS ───────────────────────────────────────────────────────────────────

def load_lyrics(path) -> list[str]:
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError:
        raise ToolError(T("core.lyrics_not_utf8")) from None
    return [l.strip() for l in lines if l.strip()]


def clean_lyric_word(word: str, uroman) -> str:
    """Remove punctuation → lowercase → uroman romanization for MMS_FA."""
    word = re.sub(r"[^\w]", "", word).lower()
    word = uroman.romanize_string(word)
    return re.sub(r"[^a-z']", "", word).strip()


def extend_line_ends(cues: list[list], duration: float, hold: float, gap: float):
    """Extend cue ends by up to `hold` s without running into the next line."""
    if hold <= 0 or not cues:
        return
    for cur, nxt in zip(cues, cues[1:]):
        cur[1] = max(cur[1], min(cur[1] + hold, nxt[0] - gap))
    cues[-1][1] = max(cues[-1][1], min(cues[-1][1] + hold, duration))


# ─── JOB / INPUT ──────────────────────────────────────────────────────────────

@dataclass
class JobOptions:
    language:      str   = "auto"      # auto, de, en, ar, tr
    mode:          str   = "sentence"  # sentence, word, raw
    use_filter:    bool  = True
    prompt_mode:   str   = "ini"       # ini, custom, none
    custom_prompt: str   = ""
    max_chars:     int   = 45
    max_lines:     int   = 1
    keep_json:     bool  = False
    line_hold:     float = 0.0
    live_lines:    bool  = True        # print recognized lines during transcription

    @classmethod
    def from_config(cls, cfg: Config) -> "JobOptions":
        return cls(language=cfg.default_language, mode=cfg.default_mode, use_filter=cfg.default_filter,
                   prompt_mode=cfg.prompt_mode, custom_prompt=cfg.custom_prompt,
                   max_chars=cfg.default_chars, max_lines=cfg.default_lines, keep_json=cfg.keep_json,
                   line_hold=cfg.line_hold, live_lines=cfg.live_lines)


def resolve_input(path, cfg: Config) -> tuple[str, Path, Path]:
    """
    What should happen with the file?  → (kind, input, output)
      lyrics : <name>.lyric.txt is next to it (or the .lyric.txt itself was chosen)
      json   : WhisperX JSON → only rebuild the SRT
      speech : everything else → transcribe
    """
    p = Path(path)
    name_l = p.name.lower()
    if name_l.endswith(cfg.lyrics_input.lower()):
        base = p.name[:-len(cfg.lyrics_input)]
        media = sorted(c for c in p.parent.glob(f"{glob_escape(base)}.*") if c.suffix.lower() in MEDIA_EXT)
        if not media:
            raise ToolError(T("core.no_media", name=p.name))
        p = media[0]
    lyrics = p.with_name(p.stem + cfg.lyrics_input)
    if lyrics.is_file():
        return "lyrics", p, p.with_name(p.stem + cfg.lyrics_suffix)
    if p.suffix.lower() == ".json":
        return "json", p, p.with_name(p.stem + cfg.speech_suffix)
    return "speech", p, p.with_name(p.stem + cfg.speech_suffix)


def glob_escape(s: str) -> str:
    return re.sub(r'([\[\]*?])', r'[\1]', s)


# ─── ENGINE ───────────────────────────────────────────────────────────────────

class Engine:
    """Keeps the models in memory and processes files one after another."""

    def __init__(self, cfg: Config, log=print, progress=None):
        self.cfg       = cfg
        self.log       = log
        self.progress  = progress or (lambda stage, pct: None)
        self.cancel    = threading.Event()
        self._whisper  = None
        self._wname    = None
        self._align: dict = {}
        self._mms      = None
        self._uroman   = None
        self._device   = None

    # ── Management ─────────────────────────────────────────────────────────────

    def set_config(self, cfg: Config):
        """Take over a new INI; reload models only if model parameters change."""
        if cfg.model_signature() != self.cfg.model_signature():
            self.unload()
        self.cfg = cfg

    def unload(self, keep=()):
        if "whisper" not in keep:
            self._whisper, self._wname = None, None
        if "align" not in keep:
            self._align.clear()
        if "mms" not in keep:
            self._mms = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    @property
    def device(self) -> str:
        if self._device is None:
            import torch
            self._device = self.cfg.device
            if self._device == "cuda" and not torch.cuda.is_available():
                self.log(i18n.line("warn", "core.no_cuda"))
                self._device = "cpu"
            if self._device == "cuda":
                self.log(i18n.tag("device") + torch.cuda.get_device_name(0))
        return self._device

    def _check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _oom_retry(self, fn, keep):
        """On 'CUDA out of memory', unload all other models once and try again."""
        try:
            return fn()
        except Exception as e:
            if "out of memory" not in str(e).lower():
                raise
            self.log(i18n.line("warn", "core.oom"))
            self.unload(keep=keep)
            return fn()

    # ── Models ─────────────────────────────────────────────────────────────────

    def whisper(self, name: str):
        if self._whisper is not None and self._wname == name:
            return self._whisper
        import whisperx
        import whisperx.asr     # loads pyannote/lightning – quiet their loggers afterwards
        quiet_library_logs()
        if self._whisper is not None:
            self.log(i18n.line("model", "core.model_switch", name=name))
            self.unload(keep=("align", "mms"))
        cfg     = self.cfg
        compute = cfg.compute_type
        if self.device == "cpu" and compute == "float16":
            compute = "float32"
        problem, hint = check_model(name)
        if problem:
            raise ToolError(problem)
        if hint:
            self.log(i18n.tag("warn") + hint)
        self.log(i18n.line("model", "core.model_load", name=name, compute=compute, beam=cfg.beam_size))
        t = time.perf_counter()
        try:
            self._whisper = whisperx.load_model(
                name, self.device, compute_type=compute,
                asr_options={"beam_size": cfg.beam_size, "suppress_numerals": cfg.suppress_numerals},
                vad_method=cfg.vad_method,
                vad_options={"chunk_size": cfg.chunk_size, "vad_onset": cfg.vad_onset, "vad_offset": cfg.vad_offset},
            )
        except Exception as e:
            if "/" in name and not Path(name).exists():        # Hugging Face repo
                raise ToolError(T("core.model_hf_failed", name=name, err=f"{e.__class__.__name__}: {e}")) from e
            raise
        self._wname = name
        self.log(i18n.line("model", "core.model_ready", time=i18n.secs(time.perf_counter() - t)))
        return self._whisper

    def align_model(self, lang: str):
        if lang not in self._align:
            import whisperx
            self.log(i18n.line("align", "core.align_load", lang=lang))
            try:
                self._align[lang] = whisperx.load_align_model(language_code=lang, device=self.device)
            except ValueError as e:
                self.log(i18n.tag("warn") + str(e))
                self._align[lang] = None
        return self._align[lang]

    def mms(self):
        if self._mms is None:
            import torchaudio
            bundle = torchaudio.pipelines.MMS_FA
            self.log(i18n.line("model", "core.mms_load"))
            self._mms = (bundle.get_model(with_star=False).to(self.device),
                         bundle.get_tokenizer(), bundle.get_aligner())
        return self._mms

    def uroman(self):
        if self._uroman is None:
            import uroman
            self._uroman = uroman.Uroman()
        return self._uroman

    # ── Steps ──────────────────────────────────────────────────────────────────

    def transcribe(self, audio: np.ndarray, opts: JobOptions) -> tuple[dict, LangRules]:
        import whisperx
        cfg = self.cfg

        # determine the language per file – otherwise WhisperX would use the language
        # of the first file for all following ones
        if opts.language == "auto":
            detector = self._whisper if self._whisper is not None else self.whisper(cfg.model)
            lang = detector.detect_language(audio)
            self.log(i18n.line("lang", "core.lang_detected", lang=lang,
                               name=T(f"speechlang.{lang}") if lang in LANGS else T("core.no_rules")))
        else:
            lang = opts.language
        model = self.whisper(cfg.models.get(lang, cfg.model))

        rules  = cfg.rules(lang)
        prompt = {"ini": rules.prompt, "custom": opts.custom_prompt.strip(), "none": ""}.get(opts.prompt_mode, "")
        model.options = replace(model.options, initial_prompt=prompt or None)
        self.log(i18n.tag("prompt") + (T("core.prompt_off") if not prompt else
                                       "INI/" + lang if opts.prompt_mode == "ini" else T("core.prompt_custom"))
                 + (f" – {prompt[:70]}{'…' if len(prompt) > 70 else ''}" if prompt else ""))

        self.log(i18n.line("asr", "core.asr_running"))
        t = time.perf_counter()
        result = model.transcribe(audio, batch_size=cfg.batch_size, language=lang, chunk_size=cfg.chunk_size,
                                  verbose=opts.live_lines,
                                  progress_callback=lambda p: self.progress("asr", p))
        self.log(i18n.tag("asr") + Tn("core.asr_done", len(result.get("segments", [])),
                                       time=i18n.secs(time.perf_counter() - t)))
        self._check_cancel()

        am = self.align_model(lang)
        if am is None:     # without word timestamps → build_cues falls back to raw
            return {"segments": result.get("segments", []), "language": lang}, rules
        self.log(i18n.line("align", "core.align_words"))
        aligned = whisperx.align(result["segments"], am[0], am[1], audio, self.device,
                                 return_char_alignments=False,
                                 progress_callback=lambda p: self.progress("align", p))
        aligned["language"] = lang
        return aligned, rules

    def align_lyrics(self, audio: np.ndarray, lines: list[str], hold: float) -> tuple[list, list[int]]:
        import torch
        ur = self.uroman()
        entries = [(cw, i) for i, line in enumerate(lines) for word in line.split()
                   if (cw := clean_lyric_word(word, ur))]
        if not entries:
            raise ToolError(T("core.lyrics_no_words"))

        model, tokenizer, aligner = self.mms()
        wav = torch.from_numpy(audio).unsqueeze(0).to(self.device)
        self.log(i18n.line("align", "core.emissions"))
        with torch.inference_mode():
            emission, _ = model(wav)
        num_frames = emission.shape[1]
        duration   = audio.shape[0] / SAMPLE_RATE

        self.log(i18n.tag("align") + "CTC Forced Alignment …")
        token_spans = aligner(emission[0], tokenizer([e[0] for e in entries]))

        # word spans → line spans (min start, max end)
        line_spans: dict[int, list[int]] = {}
        for (_, li), spans in zip(entries, token_spans):
            s, e = spans[0].start, spans[-1].end
            if li in line_spans:
                line_spans[li][0] = min(line_spans[li][0], s)
                line_spans[li][1] = max(line_spans[li][1], e)
            else:
                line_spans[li] = [s, e]

        cues, missing = [], []
        for i, line in enumerate(lines):
            if i not in line_spans:
                missing.append(i + 1)
                continue
            s, e = line_spans[i]
            cues.append([s / num_frames * duration, e / num_frames * duration, line])
        extend_line_ends(cues, duration, hold, self.cfg.line_gap)
        return [(s, e, [t]) for s, e, t in cues], missing

    # ── One file ───────────────────────────────────────────────────────────────

    def process(self, path, opts: JobOptions) -> dict:
        cfg = self.cfg
        kind, src, out = resolve_input(path, cfg)
        info = {"kind": kind, "input": src, "output": out}
        duration = None                       # audio length – known when the audio was loaded

        if kind == "json":
            self.log(i18n.line("json", "core.json_rebuild", name=src.name))
            result = json.loads(src.read_text(encoding="utf-8"))
            lang = result.get("language") or (opts.language if opts.language != "auto" else "de")
            cues, mode = build_cues(result, opts.mode, cfg.rules(lang), cfg.layout(opts.max_chars, opts.max_lines))
            if "{" in cfg.speech_suffix:
                out = src.with_name(src.stem + fill_suffix(cfg.speech_suffix, result.get("model", "json"), lang))

        elif kind == "lyrics":
            lyrics_file = src.with_name(src.stem + cfg.lyrics_input)
            lines = load_lyrics(lyrics_file)
            self.log(i18n.tag("lyrics") + Tn("core.lyrics_lines", len(lines), name=lyrics_file.name))
            audio = load_audio(src, cfg.ffmpeg)
            self.log(i18n.tag("audio") + i18n.secs(audio.shape[0] / SAMPLE_RATE))
            self._check_cancel()
            cues, missing = self._oom_retry(lambda: self.align_lyrics(audio, lines, opts.line_hold), keep=("mms",))
            for n in missing:
                self.log(i18n.line("warn", "core.line_not_aligned", n=n, text=repr(lines[n - 1])))
            lang, mode = "-", "lyrics"

        else:
            self.log(i18n.line("audio", "core.audio_filtered" if opts.use_filter and cfg.filter_string else "core.audio_original"))
            audio = load_audio(src, cfg.ffmpeg, cfg.filter_string if opts.use_filter else None)
            self.log(i18n.tag("audio") + i18n.secs(audio.shape[0] / SAMPLE_RATE))
            self._check_cancel()
            result, rules = self._oom_retry(lambda: self.transcribe(audio, opts), keep=("whisper", "align"))
            lang = rules.lang
            result["model"] = self._wname
            if "{" in cfg.speech_suffix:          # e.g. ".{model}.srt" – one SRT per model
                out = src.with_name(src.stem + fill_suffix(cfg.speech_suffix, self._wname, lang))
            if opts.keep_json:
                json_out = src.with_name(src.stem + ".json")
                json_out.write_text(json.dumps(result, ensure_ascii=False, default=_json_default), encoding="utf-8")
                self.log(i18n.line("json", "core.json_saved", name=json_out.name))
            cues, mode = build_cues(result, opts.mode, rules, cfg.layout(opts.max_chars, opts.max_lines))
            duration = audio.shape[0] / SAMPLE_RATE
            if mode != opts.mode:
                self.log(i18n.line("warn", "core.no_word_times", mode=T(f"mode.{mode}")))
            if cfg.gap_check:
                info["gap_list"] = self._report_gaps(audio, result, src)    # also for DaVinci Resolve
                info["gaps"] = len(info["gap_list"])

        if kind != "lyrics" and cfg.score_threshold > 0:    # speech and JSON: the words have scores
            info["score_list"] = self._report_score(result, src)            # also for DaVinci Resolve
            info["unsure"] = len(info["score_list"])

        cues = display_cues(cues, mode, cfg, duration)      # pause rule – lyrics keep their own
        if mode == "sentence":
            info["fast"] = self._report_fast(cues)
        write_srt(out, cues, cfg.encoding)
        self.log(i18n.tag("done") + Tn("core.written", len(cues), name=out.name))
        info.update(language=lang, mode=mode, cues=len(cues), output=out)
        return info

    def _report_fast(self, cues) -> int:
        """Warn about subtitles that are too fast to read (fixed limit, no setting)."""
        max_cps = READING_CPS
        fast = fast_cues(cues, max_cps)
        if not fast:
            return 0
        tag = i18n.tag("warn")
        self.log(tag + Tn("core.fast_found", len(fast), cps=f"{max_cps:g}"))
        for s, e, cps in fast[:15]:
            self.log(f"{tag}  {fmt_pos(s)} – {fmt_pos(e)}   (" + T("core.fast_item", cps=f"{cps:.0f}") + ")")
        if len(fast) > 15:
            self.log(f"{tag}  … " + T("core.gap_more", n=len(fast) - 15))
        self.log(tag + T("core.fast_tip"))
        return len(fast)

    def _report_gaps(self, audio, result, src: Path) -> list:
        cfg = self.cfg
        gaps = find_gaps(audio, result, cfg.gap_min, cfg.gap_margin)
        gap_file = src.with_name(src.stem + T("core.gap_suffix"))
        if not gaps:
            self.log(i18n.tag("gap") + "✓ " + T("core.gap_none"))
            gap_file.unlink(missing_ok=True)          # remove the list of an earlier run
            return []
        total = sum(e - s for s, e, _ in gaps)
        tag = i18n.tag("gap")
        self.log(tag + Tn("core.gap_found", len(gaps), total=i18n.secs(total, 0)))
        for s, e, rel in gaps[:15]:
            self.log(f"{tag}  {fmt_pos(s)} – {fmt_pos(e)}   ("
                     + T("core.gap_item", len=i18n.secs(e - s), db=f"{rel:+.0f}") + ")")
        if len(gaps) > 15:
            self.log(f"{tag}  … " + T("core.gap_more", n=len(gaps) - 15))
        if cfg.gap_srt:
            write_gap_srt(gap_file, gaps, cfg.encoding)
            self.log(tag + T("core.gap_file", name=gap_file.name))
        self.log(tag + T("core.gap_tip"))
        return gaps

    def _report_score(self, result, src: Path) -> list:
        """Score check: words that fit the audio badly – in the log and as <name>.unsure.srt."""
        cfg = self.cfg
        stats = score_stats(result)
        if stats is None:                         # e.g. a language without an alignment model
            self.log(i18n.line("info", "core.score_none"))
            return []
        unsure = find_unsure(result, cfg.score_threshold)
        score_file = src.with_name(src.stem + T("core.score_suffix"))
        tag = i18n.tag("score")
        self.log(tag + ("" if unsure else "✓ ")
                 + Tn("core.score_found", len(unsure), median=i18n.num(stats[0], 2), low=i18n.num(stats[1], 2),
                      threshold=i18n.num(cfg.score_threshold, 2)))
        if not unsure:
            score_file.unlink(missing_ok=True)        # remove the list of an earlier run
            return []
        for s, e, words in unsure[:15]:
            self.log(f"{tag}  {fmt_pos(s)} – {fmt_pos(e)}   ({unsure_words(words)})")
        if len(unsure) > 15:
            self.log(f"{tag}  … " + T("core.gap_more", n=len(unsure) - 15))
        if cfg.score_srt:
            write_score_srt(score_file, unsure, cfg.encoding)
            self.log(tag + T("core.gap_file", name=score_file.name))
        self.log(tag + T("core.score_tip"))
        return unsure


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)


# ─── SYSTEM CHECK ─────────────────────────────────────────────────────────────

# (module, package name, purpose → check.purpose_<module>, required)
REQUIRED = [
    ("whisperx",    "whisperx",    True),
    ("torch",       "torch",       True),
    ("torchaudio",  "torchaudio",  True),
    ("uroman",      "uroman",      True),
    ("numpy",       "numpy",       True),
    ("tkinterdnd2", "tkinterdnd2", False),
]
TESTED_WHISPERX = (3, 8)


def is_installed(mod: str) -> bool:
    """Is the package installed? (without loading it)"""
    import importlib.util
    import sys
    if mod in sys.modules:
        return True
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


STANDARD_MODELS = {"tiny", "base", "small", "medium", "large", "large-v1", "large-v2", "large-v3",
                   "large-v3-turbo", "turbo", "distil-large-v2", "distil-large-v3", "distil-large-v3.5"}


def _standard_models() -> set:
    try:                                   # official list from faster-whisper, if installed
        from faster_whisper import available_models
        return STANDARD_MODELS | set(available_models())
    except Exception:
        return STANDARD_MODELS


def check_model(name: str) -> tuple[str | None, str | None]:
    """
    Is the model entry usable for WhisperX? → (problem, hint)
    standard name · Hugging Face repo ("owner/name") · local CTranslate2 folder
    """
    if name in _standard_models():
        return None, None
    p = Path(name)
    if p.is_dir():
        if not (p / "model.bin").is_file():
            return T("check.no_model_bin", path=p), None
        missing = []
        if not (p / "preprocessor_config.json").is_file():
            missing.append(T("check.no_preprocessor"))
        if not (p / "tokenizer.json").is_file():
            missing.append(T("check.no_tokenizer"))
        return None, (f"{p.name}: " + " · ".join(missing)) if missing else None
    if re.fullmatch(r"[\w.-]+/[\w.-]+", name):
        return None, None                 # Hugging Face repo – downloaded on first use
    if re.search(r"[\\/:]", name):           # looks like a path but does not exist
        return T("check.model_dir_missing", name=name), None
    return None, T("check.model_unknown", name=name)


def _version(dist: str) -> str | None:
    from importlib import metadata
    for name in (dist, dist.replace("-", "_"), dist.replace("_", "-")):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return None


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v.split("+")[0])[:3])


def check_environment(cfg: Config) -> list[tuple[str, str]]:
    """
    Checks Python, packages, ffmpeg and graphics card.
    → [(level, text)] with level: ok / info / warn / error
    """
    import sys
    from wt_version import APP_NAME, VERSION
    res = [("ok", f"{APP_NAME} {VERSION} – {Path(__file__).resolve().parent}")]
    v = sys.version_info
    res.append(("ok" if v >= (3, 10) else "error",
                f"Python {v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 10) else " – " + T("check.python_old"))))

    found = {}
    for mod, dist, required in REQUIRED:
        purpose = T(f"check.purpose_{mod}")
        if not is_installed(mod):
            found[mod] = None
            res.append(("error", T("check.pkg_missing", mod=mod, purpose=purpose)) if required else
                       ("info", T("check.pkg_optional", mod=mod, purpose=purpose)))
        else:
            found[mod] = _version(dist) or "?"
            res.append(("ok", f"{mod} {found[mod]} – {purpose}"))

    wx = found.get("whisperx")
    if wx and wx != "?" and _vtuple(wx)[:2] < TESTED_WHISPERX:
        res.append(("warn", T("check.whisperx_old", version=wx)))
    t, ta = found.get("torch"), found.get("torchaudio")
    if t and ta and "?" not in (t, ta) and _vtuple(t)[:2] != _vtuple(ta)[:2]:
        res.append(("warn", T("check.torch_mismatch", torch=t, torchaudio=ta)))

    try:
        out = subprocess.run([cfg.ffmpeg, "-version"], capture_output=True, timeout=15, creationflags=NO_WINDOW)
        first = out.stdout.decode("utf-8", "replace").splitlines()[0] if out.stdout else ""
        m = re.search(r"ffmpeg version (\S+)", first)
        res.append(("ok", f"ffmpeg {m.group(1) if m else ''} – {cfg.ffmpeg}"))
    except (OSError, subprocess.TimeoutExpired, IndexError):
        res.append(("error", T("check.ffmpeg_missing", path=cfg.ffmpeg)))

    for lang, name in [("", cfg.model)] + sorted(cfg.models.items()):
        label = T("set.model") + (f" {lang}" if lang else "")
        problem, hint = check_model(name)
        if problem:
            res.append(("error", f"{label}: {problem}"))
        elif hint:
            res.append(("warn", f"{label}: {hint}"))
        elif name in _standard_models():
            res.append(("ok", f"{label}: {name}"))
        elif Path(name).is_dir():
            res.append(("ok", f"{label}: {name} ({T('check.ct2_folder')})"))
        else:
            res.append(("info", f"{label}: {name} ({T('check.hf_download')})"))

    if found.get("torch"):       # loading torch takes a few seconds – therefore last
        try:
            import torch
            if torch.cuda.is_available():
                cap = torch.cuda.get_device_capability(0)
                res.append(("ok", f"{T('check.gpu')}: {torch.cuda.get_device_name(0)} · CUDA {torch.version.cuda} · "
                                  f"sm_{cap[0]}{cap[1]}"))
            else:
                cpu_build = "+cpu" in torch.__version__ or torch.version.cuda is None
                reason = (T("check.torch_cpu", version=torch.__version__) if cpu_build else T("check.no_driver"))
                res.append(("error" if cfg.device == "cuda" else "info", T("check.no_gpu", reason=reason)))
        except Exception as e:
            res.append(("error", T("check.torch_broken", err=e)))
    return res


def quiet_library_logs():
    """Hide technical INFO messages (WhisperX, Lightning, pyannote) – warnings stay."""
    for name in ("whisperx", "lightning", "lightning.pytorch", "lightning_fabric",
                 "pytorch_lightning", "pyannote", "speechbrain"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ─── WRITING THE INI (comments are kept) ──────────────────────────────────────

_HEADER_RE    = re.compile(r'^\s*\[([^\]]+)\]\s*$')
WRAP_SECTIONS = {"prompt", "filler", "dangling", "abbreviations"}     # wrap long values onto continuation lines here


def read_section_raw(path, section: str) -> list[tuple[str, str]]:
    """Entries of a section with the original case of the keys."""
    cp = configparser.ConfigParser(delimiters=('=',), interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read(path, encoding='utf-8-sig')
    except configparser.Error:
        return []
    return [(k, v.strip()) for k, v in cp[section].items()] if cp.has_section(section) else []


def _scan(lines):
    """→ ({section: (header_line, end)}, {(section, key): (start, end)}) – all lower case, end exclusive."""
    sections, keys, cur = {}, {}, None
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HEADER_RE.match(line)
        if m:
            if cur is not None:
                sections[cur] = (sections[cur][0], i)
            cur = m.group(1).strip().lower()
            sections[cur] = (i, len(lines))
            i += 1
            continue
        s = line.strip()
        if cur is not None and s and not line[0].isspace() and s[0] not in ";#" and "=" in line:
            key = line.split("=", 1)[0].strip().lower()
            j = i + 1
            while j < len(lines) and lines[j][:1].isspace() and lines[j].strip() \
                    and lines[j].strip()[0] not in ";#":
                j += 1
            keys[(cur, key)] = (i, j)
            i = j
            continue
        i += 1
    if cur is not None:
        sections[cur] = (sections[cur][0], len(lines))
    return sections, keys


def _entry_lines(prefix: str, value: str, wrap: bool) -> list[str]:
    value = str(value).strip()
    if wrap and len(prefix) + len(value) > 100:
        parts = textwrap.wrap(value, width=max(40, 100 - len(prefix)), break_long_words=False, break_on_hyphens=False)
        return [prefix + parts[0]] + [" " * len(prefix) + p for p in parts[1:]]
    return [(prefix + value).rstrip()]


def _content_end(lines, start, end) -> int:
    """Insertion point: after the last non-empty line of the section."""
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def save_ini(path, values: dict | None = None, sections: dict | None = None):
    """
    Write values into the INI – comments, order and alignment are kept.
      values:   {(section, key): value}                set/add single values
      sections: {section: [(key, value), ...]}         refill a whole section
    A backup <name>.bak is created first.
    """
    path = Path(path)
    raw = path.read_bytes().decode("utf-8-sig") if path.exists() else ""
    newline = "\r\n" if "\r\n" in raw or not raw else "\n"
    lines = raw.splitlines()

    for sec, entries in (sections or {}).items():
        secs, keys = _scan(lines)
        s_low = sec.lower()
        if s_low not in secs:
            lines += ["", f"[{sec}]"]
            secs, keys = _scan(lines)
        # remove existing entries (from the back, so the indices stay valid)
        for (k_sec, _), (a, b) in sorted(keys.items(), key=lambda kv: -kv[1][0]):
            if k_sec == s_low:
                del lines[a:b]
        secs, _ = _scan(lines)
        start, end = secs[s_low]
        at = _content_end(lines, start, end)
        new = [line for k, v in entries for line in _entry_lines(f"{k} = ", v, False)]
        lines[at:at] = new + ([""] if at == end and at < len(lines) else [])

    for (sec, key), value in (values or {}).items():
        secs, keys = _scan(lines)
        s_low, k_low = sec.lower(), key.lower()
        wrap = s_low in WRAP_SECTIONS
        if (s_low, k_low) in keys:
            a, b = keys[(s_low, k_low)]
            first = lines[a]
            eq = first.index("=")
            prefix = first[:eq + 1] + (first[eq + 1:len(first) - len(first[eq + 1:].lstrip())] or " ")
            lines[a:b] = _entry_lines(prefix, value, wrap)
        elif s_low in secs:
            start, end = secs[s_low]
            at = _content_end(lines, start, end)
            lines[at:at] = _entry_lines(f"{key} = ", value, wrap)
        else:
            lines += ["", f"[{sec}]"] + _entry_lines(f"{key} = ", value, wrap)

    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(newline.join(lines) + newline, encoding="utf-8", newline="")
    os.replace(tmp, path)
