"""
Comment lines and long comment blocks per file, checked against tests/comment_limits.json.
python tools/comments.py shows the table; --write sets the limits to the current state.
"""

from __future__ import annotations

import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMITS = ROOT / "tests" / "comment_limits.json"
MAX_BLOCK = 3            # lines; a longer comment block counts as "long"
MAX_BLOCK_INI = 4        # lines per setting in the INI template


def files() -> list[Path]:
    found = [*ROOT.glob("*.py"), *ROOT.glob("tests/*.py"), *ROOT.glob("tools/*.py"),
             *ROOT.glob("resolve/**/*.lua"), *ROOT.glob("tests/*.lua"), ROOT / "whisper_tool.example.ini"]
    return sorted(p for p in found if p.is_file())


def _py(src: str) -> tuple[dict, set]:
    """→ ({line: comment text}, full-line comment lines) – # comments and docstrings."""
    text, full = {}, set()
    lines = src.splitlines()
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for i, t in enumerate(toks):
        if t.type == tokenize.COMMENT:
            if t.start[0] == 1 and t.string.startswith("#!"):
                continue
            text[t.start[0]] = t.string.lstrip("#").strip()
            if not lines[t.start[0] - 1][:t.start[1]].strip():
                full.add(t.start[0])
        elif t.type == tokenize.STRING:
            j = i - 1
            while j >= 0 and toks[j].type in (tokenize.NL, tokenize.COMMENT):
                j -= 1
            starts_statement = j < 0 or toks[j].type in (tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                                                         tokenize.ENCODING)
            if starts_statement and i + 1 < len(toks) and toks[i + 1].type == tokenize.NEWLINE:
                for n in range(t.start[0], t.end[0] + 1):
                    body = lines[n - 1].strip().strip('"').strip("'").strip()
                    if body:
                        text[n] = body
                        full.add(n)
    return text, full


def _lua(src: str) -> tuple[dict, set]:
    text, full, block = {}, set(), False
    for n, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if block:
            text[n] = s.replace("]]", "").strip()
            full.add(n)
            block = "]]" not in s
            continue
        if s.startswith("--[["):
            text[n] = s[4:].strip()
            full.add(n)
            block = "]]" not in s
            continue
        m = re.search(r"(?:^|\s)--(?!\[\[)(.*)$", line)
        if m and line.count('"', 0, m.start()) % 2 == 0:
            text[n] = m.group(1).strip()
            if s.startswith("--"):
                full.add(n)
    return text, full


def _ini(src: str) -> tuple[dict, set]:
    text = {n: line.strip()[1:].strip() for n, line in enumerate(src.splitlines(), 1)
            if line.strip()[:1] in (";", "#")}
    return text, set(text)


def comments(path: Path) -> tuple[dict, set]:
    """→ ({line: comment text}, full-line comment lines)."""
    src = path.read_text(encoding="utf-8")
    return {".py": _py, ".lua": _lua, ".ini": _ini}[path.suffix](src)


def blocks(full: set) -> list[tuple[int, int]]:
    """Runs of consecutive full-line comments → [(first line, length)]."""
    out = []
    for n in sorted(full):
        if out and n == out[-1][0] + out[-1][1]:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((n, 1))
    return out


def measure(path: Path) -> dict:
    text, full = comments(path)
    limit = MAX_BLOCK_INI if path.suffix == ".ini" else MAX_BLOCK
    return {"lines": len(text), "long_blocks": sum(1 for _, n in blocks(full) if n > limit)}


def current() -> dict:
    return {p.relative_to(ROOT).as_posix(): measure(p) for p in files()}


def main(argv: list[str]) -> int:
    now = current()
    if "--write" in argv:
        LIMITS.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{LIMITS.relative_to(ROOT)}: {len(now)} files")
        return 0
    old = json.loads(LIMITS.read_text(encoding="utf-8")) if LIMITS.exists() else {}
    for name, m in now.items():
        lim = old.get(name, {})
        print(f"{name:40s} lines {m['lines']:4d} / {lim.get('lines', '-'):>4}   "
              f"long blocks {m['long_blocks']:3d} / {lim.get('long_blocks', '-'):>3}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
