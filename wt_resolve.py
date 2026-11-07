"""
wt_resolve.py
=============
Connection to DaVinci Resolve – no user interface, no Resolve and no GPU needed here.

How a job travels (the Lua plugin is resolve/WhisperX_Subtitles.lua):

  1. The plugin renders the audio of the timeline into  resolve_temp/<JobID>/audio.wav
     and writes  job.txt  next to it (timeline, frame rate, start frame, In/Out …).
  2. The Workbench gets the WAV like any other file. A media file with the plugin's
     job.txt next to it is a Resolve job ("Resolve mode", see find_job).
  3. "Hand over to DaVinci" (hand_over): the SRT is shifted by the offset of the job,
     kept directly in resolve_temp/ (<Timeline>_<LANG>_<date-time>.srt) next to the SRTs of the
     previous hand-overs (KEEP_SRTS), and done.txt tells the plugin where it is and which
     language it has. The name is new for every
     hand-over: Resolve keeps using what it read the first time it imported a path.
  4. The plugin puts the SRT on a subtitle track and deletes the job folder.

Both directions use plain key=value files – Resolve's Lua has no JSON parser.
Every file the plugin reads or writes has an ASCII path: Resolve's Lua cannot open
other paths on Windows (io.open). The Workbench folder must therefore be ASCII too.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import wt_i18n as i18n
from wt_i18n import T

HERE = Path(__file__).resolve().parent
TEMP_DIR = HERE / "resolve_temp"              # job folders of the plugin + the SRTs of the last hand-overs
LEGACY_DIR = HERE / "temp" / "resolve"         # the same in 0.5.0 (temp/resolve/letzte/ held the SRT)

JOB_FILE      = "job.txt"                      # plugin → Workbench
DONE_FILE     = "done.txt"                     # Workbench → plugin (written atomically)
CANCEL_FILE   = "cancel.txt"                   # plugin: the job was cancelled
IMPORTED_FILE = "imported.txt"                 # plugin: imported, but files could not be deleted
PROGRESS_FILE = "progress.txt"                 # Workbench → plugin: where the job stands
KEEP_SRTS     = 10                             # SRTs of the last hand-overs kept in resolve_temp/
JOB_FORMAT    = "whisperx-resolve-job/1"
DONE_FORMAT   = "whisperx-resolve-done/1"
JOB_MAX_AGE_H = 72                             # older job folders are left-overs (e.g. Resolve crashed)

PLUGIN_FILE     = "WhisperX_Subtitles.lua"
PLUGIN_TEMPLATE = HERE / "resolve" / PLUGIN_FILE
CONFIG_BEGIN    = "-- >>> WX-CONFIG"
CONFIG_END      = "-- <<< WX-CONFIG <<<"

# Does the start timecode of the timeline belong into the offset of the subtitles?
# Tested so far: an SRT appended to an EMPTY subtitle track starts at the timeline start,
# its times stay relative – so only the In point counts. A test with a timeline starting
# at 01:00:00:00 decides; if the subtitles land exactly one start timecode too early,
# set this to True. Used only in srt_offset_seconds().
INCLUDE_START_TC = False


class ResolveError(RuntimeError):
    """Expected problem with an understandable message (shown as it is)."""


# ─── key=value FILES ──────────────────────────────────────────────────────────

def parse_kv(text: str) -> dict[str, str]:
    """ "key=value" per line; empty lines and lines starting with # or ; are ignored."""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "#;" or "=" not in s:
            continue
        key, value = s.split("=", 1)
        out[key.strip().lower()] = value.strip()
    return out


def read_kv(path) -> dict[str, str]:
    return parse_kv(Path(path).read_text(encoding="utf-8-sig", errors="replace"))


def format_kv(values: dict, header: str = "") -> str:
    lines = [f"# {header}"] if header else []
    for key, value in values.items():
        value = "" if value is None else str(value)
        lines.append(f"{key}={' '.join(value.splitlines())}")     # one value = one line
    return "\n".join(lines) + "\n"


def write_kv(path, values: dict, header: str = ""):
    """Atomic: a reader sees either nothing or the whole file (temp file, then rename)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(format_kv(values, header), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


# ─── FRAMES AND TIME ──────────────────────────────────────────────────────────

def exact_fps(fps) -> float:
    """29.97 → 30000/1001 (likewise 23.976, 47.952, 59.94, 119.88): Resolve counts NTSC rates exactly."""
    fps = parse_fps(fps)
    for base in (24, 30, 48, 60, 120):
        ntsc = base * 1000 / 1001
        if abs(fps - ntsc) < 0.005:
            return ntsc
    return fps


def parse_fps(value) -> float:
    """ "29.97", "29.97 DF", 25 → number; nonsense → ValueError."""
    if isinstance(value, (int, float)):
        fps = float(value)
    else:
        m = re.match(r"\s*(\d+(?:[.,]\d+)?)", str(value))
        if not m:
            raise ValueError(f"frame rate {value!r}")
        fps = float(m[1].replace(",", "."))
    if not 1 <= fps <= 1000:
        raise ValueError(f"frame rate {value!r}")
    return fps


def frames_to_seconds(frames: int, fps) -> float:
    return frames / exact_fps(fps)


# ─── JOB ──────────────────────────────────────────────────────────────────────

@dataclass
class ResolveJob:
    """Contents of job.txt – frames are absolute timeline frames as the API reports them."""
    folder:      Path
    audio:       str             # file name of the rendered audio in the job folder
    job_id:      str = ""
    timeline:    str = ""
    timeline_id: str = ""
    project:     str = ""
    fps:         float = 25.0
    fps_text:    str = ""        # as Resolve reported it, e.g. "29.97"
    drop_frame:  bool = False
    start_frame: int = 0         # first frame of the timeline (start timecode)
    start_tc:    str = ""        # for the log only – never used for calculations
    in_frame:    int = 0         # first rendered frame (In point or timeline start)
    out_frame:   int = 0         # last rendered frame
    end_frame:   int = 0         # end of the timeline when the audio was rendered
    whole:       bool = True     # True = whole timeline, False = In/Out
    language:    str = ""        # chosen in the plugin ("" = as in the Workbench window)
    mode:        str = ""        # chosen in the plugin ("" = as in the Workbench window)
    use_filter:  bool | None = None   # audio filter chosen in the plugin (None = as in the window)

    @classmethod
    def from_values(cls, folder, v: dict) -> "ResolveJob":
        """Values of job.txt → job. Missing or broken numbers → ValueError."""
        def frame(key, default=None):
            raw = v.get(key, "")
            if raw == "" and default is not None:
                return default
            return int(round(float(raw)))
        if not v.get("audio"):
            raise ValueError("audio")
        start = frame("start_frame")
        flt = v.get("filter", "").strip().lower()
        return cls(folder=Path(folder), audio=v["audio"], job_id=v.get("job", ""),
                   timeline=v.get("timeline", ""), timeline_id=v.get("timeline_id", ""),
                   project=v.get("project", ""), fps=parse_fps(v.get("fps", "")), fps_text=v.get("fps", ""),
                   drop_frame=v.get("drop_frame", "0").lower() in ("1", "true"), start_frame=start,
                   start_tc=v.get("start_tc", ""), in_frame=frame("in_frame", start),
                   out_frame=frame("out_frame", 0), end_frame=frame("end_frame", 0),
                   whole=v.get("range", "all") != "inout", language=v.get("language", "").lower(),
                   mode=v.get("mode", "").lower(),
                   use_filter=None if flt == "" else flt in ("1", "true", "on", "yes"))

    def values(self) -> dict:
        """The keys of job.txt, in the order the plugin writes them."""
        return {"format": JOB_FORMAT, "job": self.job_id, "audio": self.audio, "timeline": self.timeline,
                "timeline_id": self.timeline_id, "project": self.project, "fps": self.fps_text or f"{self.fps:g}",
                "drop_frame": "1" if self.drop_frame else "0", "start_frame": self.start_frame,
                "start_tc": self.start_tc, "range": "all" if self.whole else "inout", "in_frame": self.in_frame,
                "out_frame": self.out_frame, "end_frame": self.end_frame, "language": self.language,
                "mode": self.mode, "filter": "" if self.use_filter is None else "1" if self.use_filter else "0"}

    @property
    def audio_path(self) -> Path:
        return self.folder / self.audio

    @property
    def done_path(self) -> Path:
        return self.folder / DONE_FILE

    def alive(self) -> bool:
        """False once the plugin cancelled the job or removed its folder."""
        return (self.folder / JOB_FILE).is_file() and not (self.folder / CANCEL_FILE).exists()

    def duration(self) -> float:
        """Length of the rendered range in seconds."""
        return frames_to_seconds(max(self.out_frame - self.in_frame + 1, 0), self.fps)


def find_job(media) -> ResolveJob | None:
    """
    Resolve mode: only for a file with the plugin's job.txt next to it, written for exactly
    this file. Everything else → None, and the Workbench behaves as before.
    """
    p = Path(media)
    job_file = p.parent / JOB_FILE
    if not job_file.is_file():
        return None
    try:
        values = read_kv(job_file)
    except OSError:
        return None
    if values.get("format") != JOB_FORMAT or values.get("audio", "").lower() != p.name.lower():
        return None
    try:
        return ResolveJob.from_values(p.parent, values)
    except (ValueError, TypeError):
        return None


def srt_offset_seconds(job: ResolveJob) -> float:
    """
    How far every subtitle must move so that second 0 of the rendered audio lands on the
    frame where the audio was taken from. THE one place for this rule:
      - the audio starts at the In point → the In offset relative to the timeline start counts
      - the start timecode of the timeline counts only with INCLUDE_START_TC (open question,
        see the constant)
    Frames from the API, never timecode strings (drop frame at 29.97 would be wrong).
    """
    frames = job.in_frame - job.start_frame
    if INCLUDE_START_TC:
        frames += job.start_frame
    return frames_to_seconds(frames, job.fps)


# ─── SRT ──────────────────────────────────────────────────────────────────────

_TIME = r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
_CUE_LINE = re.compile(rf"^(\s*){_TIME}(\s*-->\s*){_TIME}(.*)$", re.S)


def _ms(h, m, s, frac) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])


def _fmt_ms(ms: int) -> str:
    ms = max(ms, 0)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def shift_srt(text: str, seconds: float) -> tuple[str, int, float | None]:
    """
    Move all subtitles by `seconds` – only the time lines change; numbers, text, tags and
    line endings stay exactly as they are. → (text, number of subtitles, start of the first)
    """
    delta = round(seconds * 1000)
    out, cues, first = [], 0, None
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = _CUE_LINE.match(body)
        if m:
            g = m.groups()
            start, end = _ms(*g[1:5]) + delta, _ms(*g[6:10]) + delta
            cues += 1
            first = start / 1000 if first is None else min(first, start / 1000)
            line = f"{g[0]}{_fmt_ms(start)}{g[5]}{_fmt_ms(end)}{g[10]}" + line[len(body):]
        out.append(line)
    return "".join(out), cues, first


def first_cue_start(text: str) -> float | None:
    return shift_srt(text, 0)[2]


def read_srt_cues(text: str) -> list[tuple[float, float, list[str]]]:
    """SRT text → [(start, end, [lines])]; numbers and empty lines are dropped."""
    cues, cur = [], None
    for line in text.splitlines():
        m = _CUE_LINE.match(line)
        if m:
            g = m.groups()
            cur = (_ms(*g[1:5]) / 1000, _ms(*g[6:10]) / 1000, [])
            cues.append(cur)
        elif cur is not None and line.strip():
            cur[2].append(line.strip())
        elif not line.strip():
            cur = None
    return cues


def fast_srt_cues(cues, max_cps: float, label) -> list:
    """Subtitles with more characters per second than max_cps → cues for an own subtitle track."""
    out = []
    for start, end, lines in cues:
        cps = sum(len(line) for line in lines) / max(end - start, 0.001)
        if cps > max_cps + 0.05:                    # tolerance for rounding to milliseconds
            out.append((start, end, [label(cps)]))
    return out


def write_progress(job: "ResolveJob", state: str, **values) -> bool:
    """
    Where the job stands, for the waiting plugin: queued, countdown (n), running (stage, pct),
    done, error, cancelled. The job folder may be gone already – then nothing happens.
    """
    try:
        write_kv(job.folder / PROGRESS_FILE, {"state": state, **values})
        return True
    except OSError:
        return False


# ─── FILE NAMES ───────────────────────────────────────────────────────────────

TRANSLIT = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
            "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I", "ç": "c", "Ç": "C"}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def ascii_name(text: str, fallback: str = "Timeline") -> str:
    """Timeline name → ASCII file name part: "Gülşen – Röportaj 1" → "Guelsen_Roeportaj_1"."""
    s = "".join(TRANSLIT.get(c, c) for c in text)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9.-]+", "_", s).strip("._-")[:80].rstrip("._-")
    if s.upper() in _RESERVED:
        s += "_"
    return s or fallback


def lang_code(lang: str) -> str:
    """ "de" → "DE"; unknown or empty → "XX". """
    code = re.sub(r"[^A-Za-z0-9-]", "", lang or "").upper()[:8]
    return code if code and code != "-" else "XX"


def last_srt_name(timeline: str, lang: str, stamp: str = "") -> str:
    """<Timeline>_<LANG>.srt, with a stamp <Timeline>_<LANG>_<stamp>.srt"""
    return f"{ascii_name(timeline)}_{lang_code(lang)}" + (f"_{stamp}" if stamp else "") + ".srt"


def is_ascii_path(path) -> bool:
    return str(path).isascii()


# ─── HAND OVER ────────────────────────────────────────────────────────────────

@dataclass
class HandOver:
    srt:    Path          # the shifted SRT in resolve_temp/
    lang:   str           # "DE"
    cues:   int
    first:  float         # start of the first subtitle (seconds from the timeline start)
    offset: float         # applied shift in seconds
    gaps:   int = 0       # gaps handed over on an own track
    fast:   int = 0       # subtitles too fast to read, handed over on an own track
    score:  int = 0       # unsure words of the score check, handed over on an own track


def write_last_srt(last_dir, name: str, text: str, encoding: str = "utf-8", keep: int = KEEP_SRTS) -> Path:
    """
    resolve_temp/ keeps the SRTs of the last `keep` hand-overs (job folders are subfolders, they
    are not touched): the new SRT is written completely first (temp file, then rename) – only
    then are the oldest SRTs deleted, so that at most `keep` remain. If writing fails, nothing
    is deleted. "Oldest" goes by the time the file was last written.
    """
    last_dir = Path(last_dir)
    last_dir.mkdir(parents=True, exist_ok=True)
    target = last_dir / name
    tmp = last_dir / (name + ".tmp")
    try:
        tmp.write_text(text, encoding=encoding, newline="")
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    def age(p: Path):
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0
    others = sorted((p for p in last_dir.iterdir()
                     if p.is_file() and p.suffix.lower() == ".srt" and p.name != target.name),
                    key=lambda p: (age(p), p.name), reverse=True)          # newest first
    for old in others[max(keep - 1, 0):]:                                  # the new one counts too
        try:
            old.unlink()
        except OSError:
            pass                   # still open in another program – next time
    return target


def hand_over(job: ResolveJob, srt, lang: str, encoding: str = "utf-8", last_dir=None,
              gaps=None, gap_label=None, fast_cps: float | None = None, fast_label=None,
              score=None, stamp: str | None = None) -> HandOver:
    """
    "Hand over to DaVinci": shift the SRT, keep it in resolve_temp/, write done.txt for the plugin.
    The SRT is read again from disk, so changes made in the meantime (e.g. in Subtitle Edit) count.
    Optional, each for an own subtitle track in Resolve (to check, then delete the track):
      gaps      [(start, end, level_db)] of the gap check  → <Timeline>_<LANG>_<stamp>_gaps.srt
      fast_cps  subtitles faster than this                 → <Timeline>_<LANG>_<stamp>_fast.srt
      score     [(start, end, text)] of the score check    → <Timeline>_<LANG>_<stamp>_score.srt
    They lie in the job folder, so the plugin deletes them after the import.
    Every hand-over gets new file names (stamp = date and time): Resolve does not read a path
    again that is already in the media pool – it would place the subtitles of the first import.
    """
    if not job.alive():
        raise ResolveError(T("resolve.err_gone"))
    srt = Path(srt)
    try:
        text = srt.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ResolveError(T("resolve.err_no_srt", path=srt)) from None
    offset = srt_offset_seconds(job)
    shifted, cues, first = shift_srt(text, offset)
    if not cues:
        raise ResolveError(T("resolve.err_no_cues", path=srt))
    code = lang_code(lang)
    name = last_srt_name(job.timeline, code, stamp or time.strftime("%Y%m%d-%H%M%S"))
    target = write_last_srt(Path(last_dir) if last_dir else TEMP_DIR, name, shifted, encoding)
    extra = {}
    stem = name[:-4]
    if gaps:
        gap_label = gap_label or (lambda s, e, rel: f"{e - s:.1f} s · {rel:+.0f} dB")
        extra["gaps"] = [(s + offset, e + offset, [gap_label(s, e, rel)]) for s, e, rel in gaps]
    if fast_cps:
        fast = fast_srt_cues(read_srt_cues(shifted), fast_cps, fast_label or (lambda cps: f"{cps:.0f} cps"))
        if fast:
            extra["fast"] = fast
    if score:
        extra["score"] = [(s + offset, e + offset, [text]) for s, e, text in score]
    done = {"format": DONE_FORMAT, "job": job.job_id, "srt": target, "lang": code, "cues": cues,
            "first": f"{first:.3f}", "offset": f"{offset:.3f}"}
    for key, items in extra.items():
        path = job.folder / f"{stem}_{key}.srt"
        path.write_text("".join(f"{n}\n{_fmt_ms(round(s * 1000))} --> {_fmt_ms(round(e * 1000))}\n"
                                + "\n".join(lines) + "\n\n" for n, (s, e, lines) in enumerate(items, 1)),
                        encoding=encoding)
        done[key] = path
        done[key + "_n"] = len(items)
    created = sorted(p.name for p in job.folder.iterdir()
                     if p.is_file() and p.name not in (job.audio, JOB_FILE, DONE_FILE, DONE_FILE + ".tmp"))
    done["files"] = ";".join(created)
    write_kv(job.done_path, done, header="WhisperX Subtitle Workbench - result for DaVinci Resolve")
    return HandOver(target, code, cues, first, offset, len(extra.get("gaps", [])), len(extra.get("fast", [])),
                    len(extra.get("score", [])))


def cleanup_jobs(temp_dir=None, max_age_hours: float = JOB_MAX_AGE_H, now: float | None = None) -> int:
    """
    Remove job folders that are no longer needed: cancelled, imported, empty or older than
    max_age_hours (e.g. Resolve crashed while waiting). The SRTs of the last hand-overs stay.
    """
    temp_dir = Path(temp_dir) if temp_dir else TEMP_DIR
    if not temp_dir.is_dir():
        return 0
    now = time.time() if now is None else now
    removed = 0
    for d in temp_dir.iterdir():
        if not d.is_dir() or d.is_symlink():
            continue
        try:
            files = list(d.iterdir())
            newest = max((f.stat().st_mtime for f in files), default=d.stat().st_mtime)
        except OSError:
            continue
        names = {f.name for f in files}
        if not files or CANCEL_FILE in names or IMPORTED_FILE in names or now - newest > max_age_hours * 3600:
            shutil.rmtree(d, ignore_errors=True)
            removed += not d.exists()
    return removed


def remove_legacy_dir(legacy=None) -> bool:
    """0.5.0 kept its job folders in temp/resolve/ (the SRT in temp/resolve/letzte/) – remove that."""
    legacy = Path(legacy) if legacy else LEGACY_DIR
    if not legacy.is_dir():
        return False
    shutil.rmtree(legacy, ignore_errors=True)
    try:
        legacy.parent.rmdir()                 # temp/ itself only if it is empty now
    except OSError:
        pass
    return not legacy.exists()


# ─── PLUGIN INSTALLATION ──────────────────────────────────────────────────────

def scripts_dir() -> Path:
    """Utility folder of Resolve's scripts: the plugin is listed on all pages (not only Edit)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        root = base / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Blackmagic Design" / "DaVinci Resolve" \
            / "Fusion" / "Scripts"
    else:
        root = Path.home() / ".local" / "share" / "DaVinciResolve" / "Fusion" / "Scripts"
    return root / "Utility"


def plugin_python(executable=None) -> Path:
    """The plugin starts the Workbench with pythonw.exe – no console window."""
    py = Path(executable or sys.executable)
    if py.name.lower() == "python.exe" and py.with_name("pythonw.exe").exists():
        return py.with_name("pythonw.exe")
    return py


def plugin_texts() -> dict[str, dict[str, str]]:
    """All texts of the plugin ("plugin" in lang/*.json) of every language: {code: {key: text}}."""
    out = {}
    for code, _ in i18n.available():
        flat = i18n._read(code)
        out[code] = {k[len("plugin."):]: v for k, v in flat.items() if k.startswith("plugin.")}
    return out


def lua_str(s) -> str:
    """Python string → Lua string literal (UTF-8 stays as it is)."""
    esc = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    return '"' + "".join(esc.get(c, c if ord(c) >= 32 and ord(c) != 127 else f"\\{ord(c):03d}")
                         for c in str(s)) + '"'


def config_block(workbench, python, lang: str, texts: dict, version: str) -> str:
    rows = [f"{CONFIG_BEGIN} (written by the Workbench: Settings -> DaVinci Resolve) >>>",
            "local WX = {",
            f"  version   = {lua_str(version)},",
            f"  workbench = {lua_str(workbench)},",
            f"  python    = {lua_str(python)},",
            f"  lang      = {lua_str(lang)},",
            "  texts     = {"]
    for code in sorted(texts):
        rows.append(f"    [{lua_str(code)}] = {{")
        rows += [f"      [{lua_str(k)}] = {lua_str(v)}," for k, v in sorted(texts[code].items())]
        rows.append("    },")
    rows += ["  },", "}", CONFIG_END]
    return "\n".join(rows)


def build_plugin(template: str, workbench, python, lang: str, texts: dict, version: str) -> str:
    """The template with the configuration written into it (between the WX-CONFIG markers)."""
    a = template.find(CONFIG_BEGIN)
    b = template.find(CONFIG_END)
    if a < 0 or b < a:
        raise ResolveError(f"{PLUGIN_TEMPLATE.name}: WX-CONFIG block not found")
    return template[:a] + config_block(workbench, python, lang, texts, version) + template[b + len(CONFIG_END):]


def installed_plugin(path=None) -> dict | None:
    """Installed plugin → {"version", "workbench", "lang", "path"}, or None if there is none."""
    path = Path(path) if path else scripts_dir() / PLUGIN_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    a, b = text.find(CONFIG_BEGIN), text.find(CONFIG_END)
    block = text[a:b] if 0 <= a < b else text
    info = {"path": path}
    for key in ("version", "workbench", "lang"):
        m = re.search(rf'^\s*{key}\s*=\s*"((?:[^"\\]|\\.)*)"', block, re.M)
        info[key] = re.sub(r"\\(.)", r"\1", m[1]) if m else ""
    return info


def install_plugin(version: str, lang: str, target_dir=None, workbench=None, python=None,
                   template=None) -> Path:
    """
    Copy the plugin into Resolve's script folder, with the Workbench path, the interface
    language and all plugin texts written into it – it needs no configuration.
    """
    workbench = Path(workbench or HERE)
    python = Path(python or plugin_python())
    for p in (workbench, python):
        if not is_ascii_path(p):
            raise ResolveError(T("resolve.err_ascii", path=p))
    if not python.exists():
        raise ResolveError(T("resolve.err_python", path=python))
    text = Path(template or PLUGIN_TEMPLATE).read_text(encoding="utf-8")
    text = build_plugin(text, workbench, python, lang, plugin_texts(), version)
    (workbench / TEMP_DIR.name).mkdir(parents=True, exist_ok=True)
    target_dir = Path(target_dir) if target_dir else scripts_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / PLUGIN_FILE
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")       # UTF-8 without BOM – Lua cannot read a BOM
    os.replace(tmp, target)
    return target
