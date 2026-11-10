"""
wt_files.txt lists the program files of a version: install.bat copies them, uninstall.bat removes them.
python tools/manifest.py shows the differences to git; --write writes the list of tracked files.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "wt_files.txt"


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    names = {n for n in out.decode("utf-8").split("\0") if n and (ROOT / n).is_file()}
    return sorted(names | {TARGET.name})


def listed() -> list[str]:
    try:
        return [line for line in TARGET.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def main(argv: list[str]) -> int:
    files = tracked()
    if "--write" in argv:
        TARGET.write_text("\n".join(files) + "\n", encoding="utf-8", newline="\n")
        print(f"{TARGET.name}: {len(files)} files")
        return 0
    missing, extra = sorted(set(files) - set(listed())), sorted(set(listed()) - set(files))
    for name in missing:
        print("+", name)
    for name in extra:
        print("-", name)
    return 1 if missing or extra else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
