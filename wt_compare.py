"""
wt_compare.py
=============
Text comparison of SRT files – timestamps, numbers and line breaks are ignored,
only the plain text is compared word by word.

  python wt_compare.py A.srt B.srt                    differences between A and B
  python wt_compare.py A.srt B.srt reference.srt      + who is right, error rates, shared errors
  python wt_compare.py A.srt --ref reference.srt      only A against the reference
  ... --media video.mp4                               times in the report become clickable
  ... --lang en|de|tr                                 language of the report

Colors – the same everywhere: A = blue, B = purple, reference = green.
All texts of the report are in lang/<code>.json ("rep").
"""

import argparse
import difflib
import html
import os
import re
import statistics
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wt_i18n as i18n                     # noqa: E402
from wt_i18n import T, Tn                  # noqa: E402

TIME_RE    = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
TAG_RE     = re.compile(r"<[^>]+>|\{\\[^}]*\}")              # <i>, <font …>, {\an8}
EDGE_PUNCT = "\"'“”„«»‚‘’()[]{}.,;:!?…-–—¿¡،؛؟*#"
PUNCT_END  = ".,;:!?…؟،"
TR_CHARS   = set("ğĞışŞİ")
CONTEXT    = 5        # words of context left/right
MERGE_GAP  = 2        # merge differences with fewer equal words in between
LABEL      = {"a": "A", "b": "B", "r": "Ref"}
TIME_OFF   = 1.0      # from this many seconds a word counts as "off" in time
TIME_RUN   = 4        # this many words in a row for a passage to be reported
AUDIO_EXT  = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac")
NO_WINDOW  = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Token:
    raw:  str         # as in the SRT
    norm: str         # for comparing: lower case, no punctuation at the edges
    time: float       # approximate time of the word (spread evenly within the cue)


@dataclass
class Doc:
    key:    str       # a / b / r
    path:   Path
    tokens: list

    @property
    def label(self) -> str:
        return LABEL[self.key]

    @property
    def punct(self) -> int:
        return sum(t.raw[-1:] in PUNCT_END for t in self.tokens)


# ─── READING ──────────────────────────────────────────────────────────────────

def _read_text(path) -> str:
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def read_srt(path) -> tuple[list[tuple[float, float, str]], bool]:
    """→ ([(start, end, text), …], turkish?)"""
    text = _read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        for i, line in enumerate(lines):
            m = TIME_RE.search(line)
            if m:
                g = m.groups()
                cue_text = " ".join(TAG_RE.sub("", l) for l in lines[i + 1:]).strip()
                if cue_text:
                    cues.append((_secs(*g[:4]), _secs(*g[4:]), cue_text))
                break
    return cues, any(c in TR_CHARS for _, _, t in cues for c in t)


def normalize(word: str, turkish: bool) -> str:
    w = word.replace("’", "'").replace("‘", "'").strip(EDGE_PUNCT)
    if turkish:
        w = w.replace("I", "ı").replace("İ", "i")
    return w.lower()


def tokenize(cues, turkish: bool) -> list[Token]:
    tokens = []
    for start, end, text in cues:
        words = text.split()
        for k, word in enumerate(words):
            n = normalize(word, turkish)
            if n:                                   # do not compare pure punctuation ("-", "…")
                tokens.append(Token(word, n, start + (max(end, start) - start) * (k + 0.5) / len(words)))
    return tokens


BAND = 20.0          # seconds: how far apart matching words may be in time
INF  = 1 << 30


def _monotone(times: list[float]) -> list[float]:
    out, cur = [], float("-inf")
    for t in times:
        cur = max(cur, t)
        out.append(cur)
    return out


def _difflib_ops(x, y):
    return difflib.SequenceMatcher(None, [t.norm for t in x], [t.norm for t in y], autojunk=False).get_opcodes()


def _ops_cost(ops) -> int:
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops if tag != "equal")


def _opcodes(x: list[Token], y: list[Token]):
    """
    Word alignment X ↔ Y as difflib-compatible opcodes.
    Exact Levenshtein alignment, but only within a time band (BAND seconds) – otherwise a pure text
    match assigns words to places minutes away when text repeats (chants, refrains).
    """
    n, m = len(x), len(y)
    if n == 0 or m == 0:
        return _difflib_ops(x, y)
    tx, ty = _monotone([t.time for t in x]), _monotone([t.time for t in y])
    if tx[-1] - tx[0] < 1 or ty[-1] - ty[0] < 1:              # no usable times
        return _difflib_ops(x, y)
    import bisect
    xs, ys = [t.norm for t in x], [t.norm for t in y]
    lo, hi = [0] * (n + 1), [0] * (n + 1)
    hi[0] = bisect.bisect_right(ty, tx[0] + BAND)
    for i in range(1, n + 1):
        lo[i] = max(bisect.bisect_left(ty, tx[i - 1] - BAND), lo[i - 1])
        hi[i] = max(bisect.bisect_right(ty, tx[i - 1] + BAND), hi[i - 1])
    hi[n] = m

    prev, plo, phi = list(range(hi[0] + 1)), 0, hi[0]          # row 0: insertions only
    ptrs = [(0, None)]
    for i in range(1, n + 1):
        l, h, xi = lo[i], hi[i], xs[i - 1]
        cur, ptr = [INF] * (h - l + 1), bytearray(h - l + 1)   # 0 = diagonal, 1 = X word missing in Y, 2 = extra Y word
        for j in range(l, h + 1):
            best, p = INF, 0
            if j >= 1 and plo <= j - 1 <= phi:
                best = prev[j - 1 - plo] + (xi != ys[j - 1])
            if plo <= j <= phi and prev[j - plo] + 1 < best:
                best, p = prev[j - plo] + 1, 1
            if j > l and cur[j - 1 - l] + 1 < best:
                best, p = cur[j - 1 - l] + 1, 2
            cur[j - l], ptr[j - l] = best, p
        ptrs.append((l, ptr))
        prev, plo, phi = cur, l, h
    cost = prev[m - plo] if plo <= m <= phi else INF

    if cost >= INF or cost > 0.5 * max(n, m):                 # times do not match (e.g. a different cut)
        alt = _difflib_ops(x, y)
        if cost >= INF or _ops_cost(alt) < cost:
            return alt

    steps, i, j = [], n, m                                     # backtrace
    while i > 0 or j > 0:
        if i == 0:
            p = 2
        else:
            l, ptr = ptrs[i]
            p = ptr[j - l]
        if p == 0:
            steps.append(("=" if xs[i - 1] == ys[j - 1] else "x", i - 1, j - 1)); i -= 1; j -= 1
        elif p == 1:
            steps.append(("d", i - 1, j)); i -= 1
        else:
            steps.append(("i", i, j - 1)); j -= 1
    steps.reverse()

    ops, k = [], 0
    while k < len(steps):
        kind, i0, j0 = steps[k]
        if kind == "=":
            e = k
            while e < len(steps) and steps[e][0] == "=":
                e += 1
            ops.append(("equal", i0, steps[e - 1][1] + 1, j0, steps[e - 1][2] + 1))
        else:
            e = k
            while e < len(steps) and steps[e][0] != "=":
                e += 1
            i2 = max([s[1] + 1 for s in steps[k:e] if s[0] in "xd"], default=i0)
            j2 = max([s[2] + 1 for s in steps[k:e] if s[0] in "xi"], default=j0)
            i1 = min([s[1] for s in steps[k:e] if s[0] in "xd"], default=i0)
            j1 = min([s[2] for s in steps[k:e] if s[0] in "xi"], default=j0)
            tag = "replace" if i2 > i1 and j2 > j1 else "delete" if i2 > i1 else "insert"
            ops.append((tag, i1, i2, j1, j2))
        k = e
    return ops


def fmt_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def pct(x: float, digits: int = 1) -> str:
    return i18n.pct(x, digits)


def secs(x: float, sign: bool = False, digits: int = 1) -> str:
    return i18n.secs(x, digits, sign)


# ─── COMPARISON ───────────────────────────────────────────────────────────────

@dataclass
class Group:
    """One place of difference in the main comparison X ↔ Y."""
    ops:     list
    x1: int; x2: int; y1: int; y2: int
    time:    float
    verdict: str = ""                    # a / b / none / out  (only with A+B+reference)
    ref:     tuple | None = None         # (r1, r2) matching reference words


@dataclass
class Errors:
    """Errors of one file compared to the reference."""
    words: int = 0                       # words of the reference
    sub: int = 0                         # wrong word instead of the correct one
    dele: int = 0                        # word of the reference missing
    ins: int = 0                         # extra word
    blocks: list = field(default_factory=list)   # (r1, r2, hyp_tokens, tag)
    ops: list = field(default_factory=list)      # alignment reference ↔ file

    @property
    def total(self) -> int:
        return self.sub + self.dele + self.ins

    @property
    def wer(self) -> float:
        return self.total / max(self.words, 1)


@dataclass
class Timing:
    """Timing deviation between equal words of two files (second minus first)."""
    n: int                        # compared words
    median_abs: float             # typical deviation in seconds
    median: float                 # offset (signed): > 0 = second file later
    share_off: float              # share of words more than TIME_OFF off
    stretches: list               # (t_start, t_end, median_delta, words, text excerpt)


def _timing(pairs) -> Timing | None:
    """pairs: [(token_first, token_second)] – equal words only."""
    if len(pairs) < 10:
        return None
    d = [q.time - p.time for p, q in pairs]
    absd = sorted(abs(x) for x in d)
    smooth = [statistics.median(d[max(0, k - 2):k + 3]) for k in range(len(d))]   # smooth single outliers
    flagged = [k for k, v in enumerate(smooth) if abs(v) > TIME_OFF]
    runs, cur = [], []
    for k in flagged:
        if cur and k - cur[-1] > 3:
            runs.append(cur)
            cur = []
        cur.append(k)
    if cur:
        runs.append(cur)
    stretches = []
    for run in runs:
        if len(run) < TIME_RUN:
            continue
        k0, k1 = run[0], run[-1]
        words = " ".join(p.raw for p, _ in pairs[k0:k1 + 1][:10]) + (" …" if k1 - k0 >= 10 else "")
        stretches.append((pairs[k0][0].time, pairs[k1][0].time, statistics.median(d[k0:k1 + 1]), len(run), words))
    return Timing(len(d), statistics.median(absd), statistics.median(d), sum(x > TIME_OFF for x in absd) / len(d),
                  stretches)


def _equal_pairs(ops, first: list, second: list) -> list:
    return [(first[i1 + k], second[j1 + k]) for tag, i1, i2, j1, j2 in ops if tag == "equal" for k in range(i2 - i1)]


@dataclass
class Report:
    a: Doc
    b: Doc | None
    r: Doc | None
    x: Doc                               # main comparison: A ↔ B, without B: A ↔ reference
    y: Doc
    ops: list
    groups: list
    word_diffs: int
    format_diffs: list
    ref_span: tuple | None = None
    err: dict = field(default_factory=dict)          # "a"/"b" → Errors
    shared: list = field(default_factory=list)       # same errors in A and B: (r1, r2, hyp_tokens)
    suggestions: list = field(default_factory=list)  # (wrong, right, count, {"A","B"})
    timing: dict = field(default_factory=dict)       # "ab" (A↔B), "a"/"b" (vs. reference) → Timing
    media: str | None = None                         # link for the player (relative to the report)
    media_name: str = ""

    @property
    def similarity(self) -> float:
        return max(0.0, 1 - self.word_diffs / max(len(self.x.tokens), 1))

    @property
    def wer_a(self):
        return self.err["a"].wer if "a" in self.err else None

    @property
    def wer_b(self):
        return self.err["b"].wer if "b" in self.err else None


def _lev(a: list[str], b: list[str]) -> int:
    if len(a) * len(b) > 4_000_000:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _groups(ops, x: Doc, y: Doc) -> list[Group]:
    groups, cur = [], []
    for k, op in enumerate(ops):
        if op[0] == "equal":
            if cur and op[2] - op[1] < MERGE_GAP and k < len(ops) - 1:
                cur.append(op)
            elif cur:
                groups.append(cur)
                cur = []
        else:
            cur.append(op)
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        while g and g[-1][0] == "equal":
            g.pop()
        x1, x2, y1, y2 = g[0][1], g[-1][2], g[0][3], g[-1][4]
        t = x.tokens[x1].time if x1 < len(x.tokens) else (y.tokens[y1].time if y1 < len(y.tokens) else 0)
        if x1 == x2 and y1 < len(y.tokens):
            t = y.tokens[y1].time
        out.append(Group(g, x1, x2, y1, y2, t))
    return out


def _errors(hyp: list[Token], ref: list[Token]) -> Errors:
    e = Errors(words=len(ref))
    e.ops = _opcodes(ref, hyp)
    for tag, i1, i2, j1, j2 in e.ops:
        if tag == "equal":
            continue
        nr, nh = i2 - i1, j2 - j1
        e.sub += min(nr, nh)
        e.dele += max(0, nr - nh)
        e.ins += max(0, nh - nr)
        e.blocks.append((i1, i2, hyp[j1:j2], tag))
    return e


def compare(path_a, path_b=None, path_ref=None, media=None, report_dir=None) -> Report:
    paths = {k: p for k, p in (("a", path_a), ("b", path_b), ("r", path_ref)) if p}
    if len(paths) < 2:
        raise ValueError(T("cmpdlg.need_two"))
    raw = {k: read_srt(p) for k, p in paths.items()}
    for k, (cues, _) in raw.items():
        if not cues:
            raise ValueError(T("rep.no_cues", label=T(f"cmpdlg.{'ref' if k == 'r' else k}"), path=paths[k]))
    turkish = any(tr for _, tr in raw.values())
    docs = {k: Doc(k, Path(p).resolve(), tokenize(raw[k][0], turkish)) for k, p in paths.items()}
    a, b, r = docs["a"], docs.get("b"), docs.get("r")
    x, y = (a, b) if b else (a, r)

    ops = _opcodes(x.tokens, y.tokens)
    word_diffs, format_diffs = 0, []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            format_diffs += [(p, q) for p, q in zip(x.tokens[i1:i2], y.tokens[j1:j2]) if p.raw != q.raw]
        elif tag == "replace":
            word_diffs += _lev([t.norm for t in x.tokens[i1:i2]], [t.norm for t in y.tokens[j1:j2]])
        else:
            word_diffs += (i2 - i1) + (j2 - j1)
    rep = Report(a, b, r, x, y, ops, _groups(ops, x, y), word_diffs, format_diffs)
    if b:
        rep.timing["ab"] = _timing(_equal_pairs(ops, a.tokens, b.tokens))

    if r and r.tokens:
        cues_r = raw["r"][0]
        # the reference may cover only part (e.g. 10 corrected minutes)
        t0, t1 = min(c[0] for c in cues_r) - 0.5, max(c[1] for c in cues_r) + 0.5
        rep.ref_span = (max(t0, 0.0), t1)
        span_idx = {}
        for d in (a, b):
            if d:
                span_idx[d.key] = [i for i, t in enumerate(d.tokens) if t0 <= t.time <= t1]
                span = [d.tokens[i] for i in span_idx[d.key]]
                rep.err[d.key] = _errors(span, r.tokens)
                rep.timing[d.key] = _timing(_equal_pairs(rep.err[d.key].ops, r.tokens, span))
        if b:
            _verdicts(rep, span_idx["a"])
            ka = {(i1, i2, tuple(t.norm for t in h)): (i1, i2, h) for i1, i2, h, _ in rep.err["a"].blocks}
            kb = {(i1, i2, tuple(t.norm for t in h)) for i1, i2, h, _ in rep.err["b"].blocks}
            rep.shared = [ka[k] for k in sorted(set(ka) & kb)]
        rep.suggestions = _suggestions(rep)

    if media:
        rep.media_name = Path(media).name
        target = Path(media).resolve()
        base = Path(report_dir or a.path.parent).resolve()
        try:
            rep.media = urllib.parse.quote(os.path.relpath(target, base).replace(os.sep, "/"))
        except ValueError:                                  # different drive
            rep.media = target.as_uri()
    return rep


def _verdicts(rep: Report, span_a: list[int]):
    """Who is right at each place of difference? Via the alignment A ↔ reference."""
    a, b, r = rep.a, rep.b, rep.r
    amap = {}                                    # A index → reference index (equal words)
    for tag, i1, i2, j1, j2 in rep.err["a"].ops:
        if tag == "equal":
            for k in range(i2 - i1):
                amap[span_a[j1 + k]] = i1 + k
    first, last = (span_a[0], span_a[-1]) if span_a else (-1, -1)
    for g in rep.groups:
        if not span_a or g.x2 <= first and g.x1 < first or g.x1 > last:
            g.verdict = "out"
            continue
        left = next((amap[k] for k in range(g.x1 - 1, max(g.x1 - 60, first - 1), -1) if k in amap), None)
        right = next((amap[k] for k in range(g.x2, min(g.x2 + 60, last + 1)) if k in amap), None)
        if left is None and g.x1 <= first:
            left = -1
        if right is None and g.x2 > last:
            right = len(r.tokens)
        if left is None or right is None or right <= left:
            g.verdict = "out"
            continue
        g.ref = (left + 1, right)
        seg_r = [t.norm for t in r.tokens[left + 1:right]]
        a_ok = [t.norm for t in a.tokens[g.x1:g.x2]] == seg_r
        b_ok = [t.norm for t in b.tokens[g.y1:g.y2]] == seg_r
        g.verdict = "a" if a_ok and not b_ok else "b" if b_ok and not a_ok else "none"


def _suggestions(rep: Report) -> list:
    """Recurring misrecognitions (1–3 words) as suggested replacements."""
    found, seen = {}, set()
    for key in ("a", "b"):
        if key not in rep.err:
            continue
        for i1, i2, hyp, tag in rep.err[key].blocks:
            if tag != "replace" or not (1 <= len(hyp) <= 3 and 1 <= i2 - i1 <= 3):
                continue
            wrong = " ".join(t.raw.strip(EDGE_PUNCT) for t in hyp)
            right = " ".join(t.raw.strip(EDGE_PUNCT) for t in rep.r.tokens[i1:i2])
            if not wrong or not right:
                continue
            k = (" ".join(t.norm for t in hyp), right)
            entry = found.setdefault(k, [wrong, right, 0, set()])
            entry[3].add(LABEL[key])
            if (i1, k) not in seen:                 # count the same error in A and B only once
                seen.add((i1, k))
                entry[2] += 1
    return sorted((tuple(v) for v in found.values()), key=lambda e: (-e[2], e[0].lower()))


# ─── AUDIO FOR LISTENING ──────────────────────────────────────────────────────

def make_audio_preview(media, out, ffmpeg="ffmpeg") -> Path | None:
    """Small AAC audio track (plays in every browser – whatever the video format of the source)."""
    media, out = Path(media), Path(out)
    if out.exists() and out.stat().st_mtime >= media.stat().st_mtime:
        return out
    try:
        r = subprocess.run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(media), "-vn", "-ac", "1",
                            "-ar", "22050", "-c:a", "aac", "-b:a", "64k", str(out)],
                           capture_output=True, timeout=1800, creationflags=NO_WINDOW)
        return out if r.returncode == 0 and out.exists() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


# ─── HTML REPORT ──────────────────────────────────────────────────────────────

CSS = """
:root { --bg:#fff; --fg:#1f2328; --muted:#656d76; --line:#d0d7de; --card:#f6f8fa;
  --a:#0969da; --a-bg:#ddf4ff; --b:#8250df; --b-bg:#f3e8ff; --r:#1a7f37; --r-bg:#dafbe1;
  --bad:#cf222e; --bad-bg:#ffebe9; --fmt:#fff8c5; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8d96a0; --line:#30363d; --card:#161b22;
  --a:#58a6ff; --a-bg:#0c2d6b; --b:#bc8cff; --b-bg:#3c1e70; --r:#3fb950; --r-bg:#0f3a1d;
  --bad:#ff7b72; --bad-bg:#5a1e1e; --fmt:#3b2e0a; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.6 "Segoe UI",system-ui,sans-serif; }
main { max-width:1120px; margin:0 auto; padding:20px 16px 140px; }
h1 { font-size:22px; margin:0 0 2px; } h2 { font-size:17px; margin:30px 0 8px; scroll-margin-top:60px; }
.muted { color:var(--muted); } .small { font-size:13px; }
nav { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line);
  padding:8px 0; margin:14px 0 0; display:flex; flex-wrap:wrap; gap:6px 14px; font-size:14px; }
nav a { color:var(--fg); text-decoration:none; } nav a:hover { text-decoration:underline; }
.chip { display:inline-block; min-width:30px; text-align:center; border-radius:6px; color:#fff;
  font-weight:600; font-size:12px; padding:1px 6px; margin-right:6px; vertical-align:1px; white-space:nowrap; }
.chip.a { background:var(--a); } .chip.b { background:var(--b); } .chip.r { background:var(--r); }
.chip.ab { background:linear-gradient(90deg,var(--a) 50%,var(--b) 50%); }
.chip.i { margin:0 2px; }
table.k { border-collapse:collapse; width:100%; margin:12px 0; }
table.k th { text-align:right; font-weight:500; color:var(--muted); font-size:13px; padding:4px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
table.k th:nth-child(2) { text-align:left; }
table.k td { padding:6px 10px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; vertical-align:top; }
table.k td.f { text-align:left; white-space:normal; width:100%; }
table.k td.f .dir { font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
table.k tr.a td.f b { color:var(--a); } table.k tr.b td.f b { color:var(--b); } table.k tr.r td.f b { color:var(--r); }
.wer { font-weight:650; font-size:16px; } tr.a .wer { color:var(--a); } tr.b .wer { color:var(--b); }
.best { font-size:12px; font-weight:600; color:var(--r); margin-left:4px; }
.banner { border:1px solid var(--line); border-left:5px solid var(--muted); background:var(--card);
  border-radius:8px; padding:10px 14px; margin:12px 0; }
.banner.a { border-left-color:var(--a); } .banner.b { border-left-color:var(--b); }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; margin:12px 0; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; }
.card .v { font-size:22px; font-weight:650; } .card .k { color:var(--muted); font-size:13px; }
.wa, .wb, .wr, .wx { border-radius:4px; padding:0 3px; font-weight:600; }
.wa { background:var(--a-bg); color:var(--a); box-shadow:inset 0 -2px 0 var(--a); }
.wb { background:var(--b-bg); color:var(--b); box-shadow:inset 0 -2px 0 var(--b); }
.wr { background:var(--r-bg); color:var(--r); box-shadow:inset 0 -2px 0 var(--r); }
.wx { background:var(--bad-bg); color:var(--bad); box-shadow:inset 0 -2px 0 var(--bad); }
.miss { border:1.5px dashed; border-radius:4px; padding:0 5px; font-size:12px; font-weight:600; }
.miss.a { border-color:var(--a); color:var(--a); } .miss.b { border-color:var(--b); color:var(--b); }
.miss.r { border-color:var(--r); color:var(--r); } .miss.x { border-color:var(--bad); color:var(--bad); }
mark { background:var(--fmt); color:inherit; border-radius:3px; padding:0 2px; }
.filters { display:flex; flex-wrap:wrap; gap:6px; margin:6px 0 10px; }
.filters button { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--card); color:var(--fg);
  border-radius:16px; padding:2px 12px; cursor:pointer; }
.filters button.on { border-color:var(--fg); font-weight:600; }
.list { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.row { display:grid; grid-template-columns:78px 1fr 118px; border-top:1px solid var(--line); }
.row:first-child { border-top:none; } .row[hidden] { display:none; }
.row .t { padding:8px; color:var(--muted); font:inherit; font-size:14px; font-variant-numeric:tabular-nums;
  border:none; border-right:1px solid var(--line); background:none; text-align:left; }
button.t { cursor:pointer; color:var(--fg); } button.t:hover { background:var(--card); }
.row .s { padding:6px 10px; min-width:0; } .row .s div { padding:1px 0; }
.vd { align-self:center; justify-self:center; font-size:12px; font-weight:650; border-radius:12px; padding:2px 10px; white-space:nowrap; }
.vd.a { color:#fff; background:var(--a); } .vd.b { color:#fff; background:var(--b); }
.vd.none { color:#fff; background:var(--bad); } .vd.out { color:var(--muted); border:1px solid var(--line); }
.row.noverdict { grid-template-columns:78px 1fr; }
details { margin-top:14px; } summary { cursor:pointer; font-weight:600; }
.full p { margin:0 0 12px; } .full .pt { font-size:12px; margin-right:6px; }
.fmtlist { columns:2 340px; font-size:14px; } .fmtlist div { break-inside:avoid; padding:2px 0; }
table.sg { border-collapse:collapse; width:100%; font-size:14px; }
table.sg td, table.sg th { padding:4px 10px; border-bottom:1px solid var(--line); text-align:left; }
table.sg th { color:var(--muted); font-weight:500; font-size:13px; }
textarea { width:100%; min-height:110px; font:13px Consolas,monospace; background:var(--card); color:var(--fg);
  border:1px solid var(--line); border-radius:8px; padding:8px; margin-top:8px; }
.copy { font:inherit; font-size:13px; margin-top:6px; cursor:pointer; }
#player { position:fixed; left:50%; transform:translateX(-50%); bottom:0; width:min(1120px, 100%); z-index:10;
  background:var(--card); border:1px solid var(--line); border-bottom:none; border-radius:12px 12px 0 0;
  padding:6px 14px; box-shadow:0 -4px 18px rgba(0,0,0,.14); display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
#player audio { flex:1 1 320px; height:36px; } #player .small { overflow-wrap:anywhere; }
"""

JS = """
const pl = document.getElementById('audio');
document.querySelectorAll('[data-t]').forEach(el => el.addEventListener('click', () => {
  if (!pl) return; pl.currentTime = Math.max(0, parseFloat(el.dataset.t) - 1.5); pl.play();
}));
if (pl) pl.addEventListener('error', () => { document.getElementById('plerr').hidden = false; });
document.querySelectorAll('.filters button').forEach(btn => btn.addEventListener('click', () => {
  const box = btn.closest('section');
  box.querySelectorAll('.filters button').forEach(b => b.classList.toggle('on', b === btn));
  box.querySelectorAll('.row[data-v]').forEach(r => r.hidden = btn.dataset.f !== 'all' && r.dataset.v !== btn.dataset.f);
}));
document.querySelectorAll('.copy').forEach(btn => btn.addEventListener('click', () => {
  const ta = document.getElementById(btn.dataset.for); ta.select();
  (navigator.clipboard ? navigator.clipboard.writeText(ta.value) : Promise.reject()).then(
    () => btn.textContent = '__COPIED__', () => { document.execCommand('copy'); btn.textContent = '__COPIED__'; });
}));
"""


def _esc(tokens) -> str:
    return " ".join(html.escape(t.raw) for t in tokens)


def _mark(tokens, cls: str) -> str:
    return f'<span class="{cls}">{_esc(tokens)}</span>' if tokens else ""


def _miss(key: str) -> str:
    return f'<span class="miss {key}" title="{T("rep.missing_title")}">{T("rep.missing")}</span>'


def _chip(key: str, inline: bool = False) -> str:
    """Color chip A/B/Ref – inline: within a sentence (language file: {a}, {b}, {r})."""
    text = {"a": "A", "b": "B", "r": "Ref", "ab": "A+B", "abx": "A↔B"}[key]
    cls = "ab" if key == "abx" else key
    return f'<span class="chip {cls}{" i" if inline else ""}">{text}</span>'


def _c(**kw) -> dict:
    """Color chips as placeholders for texts from the language file."""
    return dict({k: _chip(k, inline=True) for k in ("a", "b", "r")}, **kw)


def _time_cell(rep: Report, t: float) -> str:
    if rep.media:
        return f'<button class="t" data-t="{t:.2f}" title="{T("rep.play_title")}">▶ {fmt_time(t)}</button>'
    return f'<div class="t">{fmt_time(t)}</div>'


def _line(doc: Doc, tokens, i1: int, i2: int, inner: str) -> str:
    before, after = tokens[max(i1 - CONTEXT, 0):i1], tokens[i2:i2 + CONTEXT]
    return (f'<div>{_chip(doc.key)}{"… " if before else ""}{_esc(before)} '
            f'{inner} {_esc(after)}{" …" if after else ""}</div>')


def _group_lines(rep: Report, g: Group) -> str:
    """A line, B line (or Ref line) and – if present – the matching reference."""
    x, y = rep.x, rep.y
    xs, ys = [], []
    for tag, i1, i2, j1, j2 in g.ops:
        if tag == "equal":
            xs.append(_esc(x.tokens[i1:i2]))
            ys.append(_esc(y.tokens[j1:j2]))
        else:
            xs.append(_mark(x.tokens[i1:i2], "w" + x.key) or _miss(x.key))
            ys.append(_mark(y.tokens[j1:j2], "w" + y.key) or _miss(y.key))
    out = _line(x, x.tokens, g.x1, g.x2, " ".join(xs)) + _line(y, y.tokens, g.y1, g.y2, " ".join(ys))
    if g.ref is not None and rep.r is not None:
        r1, r2 = g.ref
        seg = rep.r.tokens[r1:r2]
        out += _line(rep.r, rep.r.tokens, r1, r2, _mark(seg, "wr") or _miss("r"))
    return out


VERDICTS = ("a", "b", "none", "out")


def _verdict(k: str) -> str:
    return T(f"rep.verdict_{k}")


def _diff_section(rep: Report) -> str:
    with_verdict = rep.b is not None and rep.r is not None
    rows = []
    for g in rep.groups[:3000]:
        vd = f'<div class="vd {g.verdict}">{_verdict(g.verdict)}</div>' if with_verdict else ""
        rows.append(f'<div class="row{"" if with_verdict else " noverdict"}" data-v="{g.verdict or "all"}">'
                    f'{_time_cell(rep, g.time)}<div class="s">{_group_lines(rep, g)}</div>{vd}</div>')
    filters = ""
    if with_verdict:
        counts = {k: sum(g.verdict == k for g in rep.groups) for k in VERDICTS}
        filters = ('<div class="filters">' + f'<button class="on" data-f="all">{T("rep.filter_all")} ({len(rep.groups)})</button>'
                   + "".join(f'<button data-f="{k}">{_verdict(k)} ({counts[k]})</button>' for k in VERDICTS if counts[k])
                   + "</div>")
    if rep.b is not None:
        title = T("rep.diff_title_ab", **_c())
        intro = T("rep.diff_intro_ab") + " " + T("rep.diff_intro_ref" if rep.r else "rep.diff_intro_noref")
    else:
        title = T("rep.diff_title_ar", **_c())
        intro = T("rep.diff_intro_ar")
    empty = f'<div class="row noverdict"><div class="t"></div><div class="s">{T("rep.no_diffs")}</div></div>'
    return (f'<section id="diff"><h2>{title} – {Tn("rep.places", len(rep.groups))}</h2>'
            f'<div class="muted small">{intro}</div>{filters}<div class="list">{"".join(rows) or empty}</div></section>')


def _shared_section(rep: Report) -> str:
    if not rep.shared:
        return ""
    r = rep.r.tokens
    rows = []
    for r1, r2, hyp in rep.shared[:2000]:
        t = r[r1].time if r1 < len(r) else (hyp[0].time if hyp else 0)
        inner_x = _mark(hyp, "wx") or _miss("x")
        rows.append(f'<div class="row noverdict">{_time_cell(rep, t)}<div class="s">'
                    f'<div>{_chip("ab")}{"… " if r1 else ""}{_esc(r[max(r1 - CONTEXT, 0):r1])} '
                    f'{inner_x} {_esc(r[r2:r2 + CONTEXT])} …</div>'
                    f'{_line(rep.r, r, r1, r2, _mark(r[r1:r2], "wr") or _miss("r"))}</div></div>')
    return (f'<section id="shared"><h2>{T("rep.shared_title", **_c())} – {Tn("rep.places", len(rep.shared))}</h2>'
            f'<div class="muted small">{T("rep.shared_intro")}</div><div class="list">{"".join(rows)}</div></section>')


def _suggest_section(rep: Report) -> str:
    if not rep.suggestions:
        return ""

    def table(items):
        rows = []
        for wrong, right, count, who in items:
            warn = (f' <span class="muted" title="{T("rep.short_word")}">⚠</span>' if len(wrong) <= 3 else "")
            chips = "".join(_chip(k.lower()) for k in sorted(who))
            rows.append(f'<tr><td><span class="wx">{html.escape(wrong)}</span>{warn}</td>'
                        f'<td><span class="wr">{html.escape(right)}</span></td><td>{count}×</td><td>{chips}</td></tr>')
        return (f'<table class="sg"><tr><th>{T("rep.sg_wrong")}</th><th>{T("rep.sg_right")}</th>'
                f'<th>{T("rep.sg_count")}</th><th>{T("rep.sg_in")}</th></tr>{"".join(rows)}</table>')

    multi = [e for e in rep.suggestions if e[2] >= 2]
    once = [e for e in rep.suggestions if e[2] < 2]
    out = (f'<section id="suggest"><h2>{T("rep.sg_title")}</h2>'
           f'<div class="muted small">{T("rep.sg_intro")}</div>')
    if multi:
        lines = "\n".join(f"{w} = {r}" for w, r, _, _ in multi)
        out += (f'<h3 class="small" style="margin:14px 0 4px">{T("rep.sg_multi", n=len(multi))}</h3>{table(multi)}'
                f'<textarea id="sgtext" readonly>{html.escape(lines)}</textarea>'
                f'<button class="copy" data-for="sgtext">{T("rep.copy_all")}</button>')
    else:
        out += f'<div class="small" style="margin-top:8px">{T("rep.sg_none_multi")}</div>'
    if once:
        out += f'<details><summary class="small">{T("rep.sg_once", n=len(once))}</summary>{table(once[:200])}</details>'
    return out + "</section>"


def _format_section(rep: Report) -> str:
    x, y = rep.x, rep.y
    rows = [f'<div><span class="muted">{fmt_time(p.time)}</span> {_chip(x.key)}'
            f'{html.escape(p.raw)} {_chip(y.key)}<mark>{html.escape(q.raw)}</mark></div>'
            for p, q in rep.format_diffs[:3000]]
    none = f'<span class="muted">{T("rep.none")}</span>'
    return (f'<section id="fmt"><details><summary>{T("rep.fmt_title", places=Tn("rep.places", len(rep.format_diffs)))}'
            f'</summary><div class="fmtlist">{"".join(rows) or none}</div></details></section>')


def _full_section(rep: Report) -> str:
    """Merged text: equal words plain, deviations in the color of the file."""
    x, y = rep.x, rep.y
    out, count, para_t = [], 0, None

    def para_start(t):
        return (f'<button class="t pt" data-t="{t:.2f}" style="border:none">▶ {fmt_time(t)}</button>'
                if rep.media else f'<span class="muted pt">{fmt_time(t)}</span>')

    for tag, i1, i2, j1, j2 in rep.ops:
        if tag == "equal":
            for p, q in zip(x.tokens[i1:i2], y.tokens[j1:j2]):
                if para_t is None:
                    para_t = q.time
                    out.append(para_start(q.time))
                w = html.escape(q.raw)
                out.append(f'<mark title="{x.label}: {html.escape(p.raw)}">{w}</mark>' if p.raw != q.raw else w)
                count += 1
                if count >= 70 and q.raw.endswith((".", "?", "!", "؟")):
                    out.append("</p><p>")
                    count, para_t = 0, None
        else:
            if para_t is None:
                para_t = (x.tokens[i1].time if i1 < len(x.tokens) else y.tokens[j1].time if j1 < len(y.tokens) else 0)
                out.append(para_start(para_t))
            out.append(_mark(x.tokens[i1:i2], "w" + x.key))
            out.append(_mark(y.tokens[j1:j2], "w" + y.key))
    word = html.escape(T("rep.legend_word"))
    legend = T("rep.full_legend", wx=f'<span class="wa">{word}</span>', wy=f'<span class="w{y.key}">{word}</span>',
               wf=f"<mark>{word}</mark>", x=x.label, y=y.label)
    return (f'<section id="full"><details><summary>{T("rep.full_title")}</summary>'
            f'<div class="muted small" style="margin:6px 0 10px">{legend}</div>'
            f'<div class="full"><p>{" ".join(o for o in out if o)}</p></div></details></section>')


def _offset_text(key: str, med: float) -> str:
    """Offset always as a number – signed median, plus the direction in words."""
    val = secs(med, sign=True, digits=2)
    if round(med, 2) == 0:
        return T("rep.offset", val=val)
    if key == "ab":
        return T("rep.offset_ab_later" if med > 0 else "rep.offset_ab_earlier", val=val)
    return T("rep.offset_late" if med > 0 else "rep.offset_early", val=val, who=LABEL[key])


def _timing_card(rep: Report) -> str:
    tm = rep.timing.get("ab")
    if not tm:
        return ""
    return (f'<div class="card"><div class="v">⌀ {secs(tm.median_abs, digits=2)}</div><div class="k">'
            f'{T("rep.card_timing", **_c())}<br>{_offset_text("ab", tm.median)}<br>'
            f'{T("rep.share_off", pct=pct(tm.share_off), s=secs(TIME_OFF, digits=0))}</div></div>')


def _timing_section(rep: Report) -> str:
    keys = [k for k in ("a", "b") if rep.timing.get(k)] if rep.r is not None else ["ab"]
    keys = [k for k in keys if rep.timing.get(k)]
    if not keys:
        return ""
    off = secs(TIME_OFF, digits=0)
    summary_lines, rows = [], []
    for k in keys:
        tm = rep.timing[k]
        who = (f'{_chip("a")}↔ {_chip("b")}' if k == "ab" else T("rep.vs_ref", who=_chip(k, True), **_c()))
        summary_lines.append(f'<div>{who} ' + T("rep.timing_line", typ=secs(tm.median_abs, digits=2),
                                                 offset=_offset_text(k, tm.median), pct=pct(tm.share_off), s=off)
                             + '</div>')
        for t0, t1, med, n, words in tm.stretches:
            if k == "ab":
                what = T("rep.stretch_ab_later" if med > 0 else "rep.stretch_ab_earlier", val=secs(abs(med)))
                chip = _chip("abx")
            else:
                what = T("rep.stretch_late" if med > 0 else "rep.stretch_early", val=secs(abs(med)), who=LABEL[k])
                chip = _chip(k)
            rows.append((t0, f'<div class="row noverdict">{_time_cell(rep, t0)}<div class="s"><div>{chip}<b>{what}</b> '
                             f'<span class="muted">· {Tn("rep.words", n)} · {fmt_time(t0)}–{fmt_time(t1)}</span></div>'
                             f'<div class="muted">„{html.escape(words)}“</div></div></div>'))
    rows.sort(key=lambda r: r[0])
    body = ("".join(r for _, r in rows) if rows else
            f'<div class="row noverdict"><div class="t"></div><div class="s">{T("rep.timing_none")}</div></div>')
    note = (" " + T("rep.timing_note_ref")) if rep.r is not None else ""
    return (f'<section id="timing"><h2>{T("rep.timing_title", places=Tn("rep.places", len(rows)), s=off)}</h2>'
            f'<div class="small" style="margin-bottom:8px">{"".join(summary_lines)}</div>'
            f'<div class="muted small">{T("rep.timing_explain", s=off, n=TIME_RUN)}{note}</div>'
            f'<div class="list" style="margin-top:8px">{body}</div></section>')


def _overview(rep: Report) -> str:
    docs = [d for d in (rep.a, rep.b, rep.r) if d]
    with_ref = rep.r is not None and rep.err
    best = None
    if with_ref and "b" in rep.err and rep.err["a"].wer != rep.err["b"].wer:
        best = min(("a", "b"), key=lambda k: rep.err[k].wer)
    off = secs(TIME_OFF, digits=0)

    def th(key, **kw):
        return f'<th title="{T(f"rep.col_{key}_title", **kw)}">{T(f"rep.col_{key}", **kw)}</th>'
    head = (f'<tr><th></th><th>{T("rep.col_file")}</th><th>{T("rep.col_words")}</th>'
            + ("".join(th(k) for k in ("wer", "sub", "del", "ins", "typ", "offset")) + th("off", s=off)
               if with_ref else "")
            + th("punct") + '</tr>')
    rows = []
    for d in docs:
        cells = ""
        if with_ref:
            if d.key in rep.err:
                e = rep.err[d.key]
                tag = f'<span class="best">{T("rep.better")}</span>' if best == d.key else ""
                tm = rep.timing.get(d.key)
                cells = (f'<td><span class="wer">{pct(e.wer)}</span>{tag}</td><td>{e.sub}</td><td>{e.dele}</td>'
                         f'<td>{e.ins}</td>' + (f'<td>{secs(tm.median_abs, digits=2)}</td>'
                                                f'<td>{secs(tm.median, sign=True, digits=2)}</td><td>{pct(tm.share_off)}</td>'
                                                if tm else '<td>–</td><td>–</td><td>–</td>'))
            else:
                cells = f'<td class="muted">{T("rep.benchmark")}</td>' + '<td></td>' * 6
        rows.append(f'<tr class="{d.key}"><td>{_chip(d.key)}</td>'
                    f'<td class="f"><b>{html.escape(d.path.name)}</b><div class="dir">{html.escape(str(d.path.parent))}</div></td>'
                    f'<td>{len(d.tokens)}</td>{cells}<td>{d.punct}</td></tr>')
    table = f'<table class="k">{head}{"".join(rows)}</table>'

    if with_ref and rep.b:
        ea, eb = rep.err["a"], rep.err["b"]
        if best:
            w, l = (eb, ea) if best == "b" else (ea, eb)
            less = (l.total - w.total) / max(l.total, 1)
            banner = (f'<div class="banner {best}">{_chip(best)}' +
                      T("rep.banner_best", wer_w=pct(w.wer), wer_l=pct(l.wer), n_w=w.total, n_l=l.total,
                        less=pct(less, 0)) + '</div>')
        else:
            banner = f'<div class="banner">{T("rep.banner_equal", wer=pct(ea.wer))}</div>'
        shared_n = sum(max(len(h), r2 - r1) for r1, r2, h in rep.shared)
        banner += f'<div class="muted small">{T("rep.banner_shared", n=shared_n)}</div>'
    elif with_ref:
        ea = rep.err["a"]
        banner = (f'<div class="banner a">{_chip("a")}'
                  + T("rep.banner_a", wer=pct(ea.wer), n=ea.total, words=ea.words) + '</div>')
    else:
        banner = f'<div class="banner">{T("rep.banner_noref")}</div>'
    span = (f'<div class="muted small">{T("rep.ref_span", t0=fmt_time(rep.ref_span[0]), t1=fmt_time(rep.ref_span[1]), n=len(rep.r.tokens))}'
            f'</div>') if rep.ref_span else ""

    cards = ""
    if rep.b:
        cards = ('<div class="cards">'
                 f'<div class="card"><div class="v">{pct(rep.similarity)}</div><div class="k">'
                 f'{T("rep.card_similarity", **_c())}</div></div>'
                 f'<div class="card"><div class="v">{rep.word_diffs}</div><div class="k">'
                 f'{Tn("rep.card_diffs", rep.word_diffs, places=Tn("rep.places", len(rep.groups)))}</div></div>'
                 f'<div class="card"><div class="v">{len(rep.format_diffs)}</div><div class="k">{T("rep.card_format")}'
                 f'</div></div>{_timing_card(rep)}</div>')
    return table + banner + span + cards


def write_report(rep: Report, *args) -> Path:
    """write_report(rep, output.html) – older calls (rep, a, b, ref, output) work too."""
    out = Path(args[-1])
    nav = [("#diff", T("rep.nav_diff") if rep.b else T("rep.nav_errors_a"))]
    if rep.shared:
        nav.append(("#shared", T("rep.nav_shared")))
    if rep.suggestions:
        nav.append(("#suggest", T("rep.nav_suggest")))
    if any(rep.timing.values()):
        nav.append(("#timing", T("rep.nav_timing")))
    nav += [("#fmt", T("rep.nav_format")), ("#full", T("rep.nav_full"))]
    player = ""
    if rep.media:
        player = (f'<div id="player"><div class="small"><b>▶ {T("rep.listen")}</b><div class="muted">'
                  f'{T("rep.listen_hint")}</div></div>'
                  f'<audio id="audio" controls preload="metadata" src="{rep.media}" title="{html.escape(rep.media_name)}"></audio>'
                  f'<div id="plerr" class="small" style="color:var(--bad)" hidden>{T("rep.play_error")}</div></div>')
    title = " ↔ ".join(d.label for d in (rep.a, rep.b, rep.r) if d)
    js = JS.replace("__COPIED__", html.escape(T("rep.copied")).replace("'", "\\'"))
    doc = f"""<!doctype html>
<html lang="{T("_meta.html_lang")}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{T("rep.h1")} {html.escape(title)} – {html.escape(rep.a.path.stem)}</title><style>{CSS}</style></head><body><main>
<h1>{T("rep.h1")}</h1>
<div class="muted small">{T("rep.intro")} {T("rep.colors", **_c())}</div>
{_overview(rep)}
<nav>{" · ".join(f'<a href="{h}">{t}</a>' for h, t in nav)}</nav>
{_diff_section(rep)}{_shared_section(rep)}{_suggest_section(rep)}{_timing_section(rep)}{_format_section(rep)}{_full_section(rep)}
</main>{player}<script>{js}</script></body></html>"""
    out.write_text(doc, encoding="utf-8")
    return out


def report_name(path_a, path_b) -> Path:
    A, B = Path(path_a), Path(path_b)
    return A.with_name(f"{A.stem} vs {B.stem}{T('rep.file_suffix')}.html")


def summary(rep: Report, short: bool = False) -> str:
    """One line for the log – short: only the first part (status line)."""
    if rep.b:
        s = T("rep.sum_ab", sim=pct(rep.similarity), diffs=Tn("rep.n_diffs", rep.word_diffs),
              places=Tn("rep.places", len(rep.groups)), fmt=len(rep.format_diffs))
    else:
        s = T("rep.sum_ar", places=Tn("rep.n_error_places", len(rep.groups)))
    if short:
        return s
    if rep.wer_a is not None:
        s += " · " + T("rep.sum_wer", wer=pct(rep.wer_a)) + (f" / B {pct(rep.wer_b)}" if rep.wer_b is not None else "")
    tm = rep.timing.get("ab")
    if tm:
        s += " · " + T("rep.sum_time", typ=secs(tm.median_abs, digits=2), offset=_offset_text("ab", tm.median))
    return s


def main():
    ini = Path(__file__).with_name("whisper_tool.ini")
    if not ini.exists():
        ini = ini.with_name("whisper_tool.example.ini")
    lang = next((sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == "--lang"), None)
    i18n.load(lang or i18n.ini_language(ini))
    ap = argparse.ArgumentParser(description=T("cli.cmp_description"))
    ap.add_argument("files", nargs="+", help=T("cli.cmp_files"))
    ap.add_argument("--ref", help=T("cli.cmp_ref"))
    ap.add_argument("--media", help=T("cli.cmp_media"))
    ap.add_argument("--ffmpeg", help=T("cli.cmp_ffmpeg"))
    ap.add_argument("--lang", help=T("cli.cmp_lang"))
    args = ap.parse_args()
    if len(args.files) > 3 or (len(args.files) == 3 and args.ref):
        ap.error(T("cli.cmp_too_many"))
    fa = args.files[0]
    fb = args.files[1] if len(args.files) > 1 else None
    fr = args.ref or (args.files[2] if len(args.files) == 3 else None)
    if not fb and not fr:
        ap.error(T("cli.cmp_too_few"))
    for f in (fa, fb, fr, args.media):
        if f and not Path(f).is_file():
            sys.exit(T("cli.not_found", path=f))
    out_path = report_name(fa, fb or fr)
    media = args.media
    if media and Path(media).suffix.lower() not in AUDIO_EXT:
        ffmpeg = args.ffmpeg
        if not ffmpeg:                       # like the tool: [System] ffmpeg_path from the INI next to it
            import configparser
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            cp.read(ini, encoding="utf-8-sig")
            ffmpeg = cp.get("System", "ffmpeg_path", fallback="").strip().strip('"')
            ffmpeg = ffmpeg if ffmpeg and Path(ffmpeg).is_file() else "ffmpeg"
        media = make_audio_preview(media, out_path.with_suffix(".m4a"), ffmpeg) or media
    try:
        result = compare(fa, fb, fr, media=media, report_dir=out_path.parent)
    except (ValueError, OSError) as e:
        sys.exit(T("cli.error", err=e))
    print(summary(result))
    print(T("cmpdlg.report", path=write_report(result, out_path)))


if __name__ == "__main__":
    main()
