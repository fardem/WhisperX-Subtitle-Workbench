"""
wt_i18n.py
==========
Language files for the user interface, log messages and the comparison report.

  lang/<code>.json   one file per language (de, en, tr, ...) - new languages: copy en.json,
                     translate the values, set "_meta" -> "name". Nothing else to register.

Values are plain strings or lists of lines (joined with line breaks). Keys may be nested;
"a": {"b": "..."} is addressed as "a.b". Placeholders: {name}. Plural forms: {"one": ..., "other": ...}.
Missing keys fall back to English, then German, then the key itself - a half-finished
translation never breaks the tool.
"""

from __future__ import annotations

import json
import locale
import os
import sys
from pathlib import Path

LANG_DIR = Path(__file__).resolve().parent / "lang"
FALLBACKS = ("en", "de")

_cur: dict = {}
_fallback: list[dict] = []
_code = ""
errors: list[str] = []          # problems while loading (shown in the log by the GUI)


class _Safe(dict):
    """Unknown placeholders stay as they are, e.g. "{model}" in a tooltip."""
    def __missing__(self, key):
        return "{" + key + "}"


def _flatten(d: dict, prefix: str = "", out: dict | None = None) -> dict:
    out = {} if out is None else out
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            _flatten(v, key + ".", out)
        elif isinstance(v, list):
            out[key] = "\n".join(str(x) for x in v)
        else:
            out[key] = str(v)
    return out


def _read(code: str) -> dict:
    path = LANG_DIR / f"{code}.json"
    try:
        return _flatten(json.loads(path.read_text(encoding="utf-8-sig")))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        errors.append(f"{path.name}: {e}")
        return {}


def available() -> list[tuple[str, str]]:
    """[(code, native name)] of all language files, sorted by code."""
    out = []
    for p in sorted(LANG_DIR.glob("*.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8-sig")).get("_meta", {})
        except (OSError, ValueError):
            continue
        out.append((p.stem, meta.get("name", p.stem)))
    return out


def detect() -> str:
    """Language of the operating system, if a language file exists for it - else English."""
    codes = {c for c, _ in available()}
    cand = []
    if sys.platform == "win32":
        try:
            import ctypes
            cand.append(locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), ""))
        except Exception:
            pass
    cand += [os.environ.get(v, "") for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")]
    try:
        cand.append(locale.getlocale()[0] or "")
    except Exception:
        pass
    for c in cand:
        c = c.split(":")[0].split(".")[0].replace("-", "_").split("_")[0].lower()
        c = {"german": "de", "english": "en", "turkish": "tr"}.get(c, c)
        if c in codes:
            return c
    return "en"


def load(code: str = "auto") -> str:
    """Activate a language ("auto" = system language). Returns the code actually used."""
    global _cur, _fallback, _code
    code = (code or "auto").strip().lower()
    if code == "auto" or not (LANG_DIR / f"{code}.json").is_file():
        if code != "auto":
            errors.append(f"lang/{code}.json not found")
        code = detect()
    _cur = _read(code)
    _fallback = [_read(c) for c in FALLBACKS if c != code]
    _code = code
    return code


def ini_language(ini_path) -> str:
    """[System] ui_language from the INI - read before anything else, so that even
    INI warnings appear in the chosen language."""
    import configparser
    cp = configparser.ConfigParser(delimiters=("=",), interpolation=None, strict=False)
    try:
        cp.read(ini_path, encoding="utf-8-sig")
        return cp.get("System", "ui_language", fallback="auto").strip() or "auto"
    except (configparser.Error, OSError, UnicodeDecodeError):
        return "auto"


def current() -> str:
    if not _code:
        load()
    return _code


def _lookup(key: str) -> str | None:
    if not _code:
        load()
    if key in _cur:
        return _cur[key]
    for d in _fallback:
        if key in d:
            return d[key]
    return None


def has(key: str) -> bool:
    return _lookup(key) is not None


def T(key: str, **kw) -> str:
    s = _lookup(key)
    if s is None:
        return key
    return s.format_map(_Safe(kw)) if kw else s


def Tn(key: str, n: int, **kw) -> str:
    """Plural: key.one for n == 1, otherwise key.other (n is available as {n})."""
    s = _lookup(f"{key}.one" if n == 1 else f"{key}.other")
    if s is None:
        s = _lookup(f"{key}.other") or _lookup(key) or key
    return s.format_map(_Safe(dict(kw, n=n)))


def all_values(key: str) -> set[str]:
    """Value of a key in every language file (e.g. all known names of the gap file)."""
    vals = set()
    for code, _ in available():
        v = _read(code).get(key)
        if v:
            vals.add(v)
    return vals


# ── Numbers ───────────────────────────────────────────────────────────────────

def num(x: float, digits: int = 1, sign: bool = False) -> str:
    s = f"{x:+.{digits}f}" if sign else f"{x:.{digits}f}"
    if sign and float(s) == 0:
        s = "±" + s[1:]
    return s.replace(".", T("_meta.decimal")) if T("_meta.decimal") != "_meta.decimal" else s


def pct(x: float, digits: int = 1) -> str:
    """x as a fraction (0.06 -> "6,0 %" / "6.0%" / "%6,0")."""
    return T("_meta.percent", v=num(x * 100, digits))


def secs(x: float, digits: int = 1, sign: bool = False) -> str:
    return T("_meta.seconds", v=num(x, digits, sign))


# ── Log prefixes ──────────────────────────────────────────────────────────────

TAGS = ("info", "warn", "error", "done", "check", "gap", "dnd", "compare", "device",
        "model", "align", "lang", "prompt", "asr", "json", "lyrics", "audio")


def tag(kind: str) -> str:
    """Log prefix with fixed width: "[warn]    " - the GUI colors lines by this prefix."""
    return f"[{T('tag.' + kind)}] ".ljust(10)


def line(kind: str, key: str, **kw) -> str:
    return tag(kind) + T(key, **kw)
