"""Test helpers: hand-made WhisperX results and SRT files."""

from pathlib import Path

import wt_core

ROOT = Path(__file__).resolve().parent.parent


def make_result(text: str, start: float = 0.0, step: float = 0.4, length: float = 0.3,
                pauses: dict | None = None, language: str = "de") -> dict:
    """
    WhisperX-like result with one segment and word timestamps.
    Every word lasts `length` s, the next one starts `step` s later.
    pauses = {word_index: extra_seconds} inserts a pause before that word.
    """
    words, t = [], start
    for i, w in enumerate(text.split()):
        t += (pauses or {}).get(i, 0.0)
        words.append({"word": w, "start": round(t, 3), "end": round(t + length, 3)})
        t += step
    end = words[-1]["end"] if words else start
    return {"segments": [{"start": start, "end": end, "text": text, "words": words}], "language": language}


def write_srt(path: Path, cues) -> Path:
    """cues = [(start, end, text)] → SRT file."""
    wt_core.write_srt(path, [(s, e, [t]) for s, e, t in cues])
    return path
