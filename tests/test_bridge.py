"""Close gaps (flicker-free, e.g. DaVinci Resolve): a subtitle stays until the next one starts."""

import json
import re
import shutil

import pytest

import wt_core as core
from helpers import ROOT, make_result


def test_short_gap_is_closed_long_pause_stays():
    cues = [(0.0, 1.0, ["Eins"]), (1.1, 2.0, ["Zwei"]), (10.0, 11.0, ["Drei"])]
    assert core.bridge_gaps(cues, 2.0) == [(0.0, 1.1, ["Eins"]), (1.1, 2.0, ["Zwei"]), (10.0, 11.0, ["Drei"])]


def test_starts_never_move():
    cues = [(0.0, 0.2, ["a"]), (0.5, 0.6, ["b"]), (0.65, 0.9, ["c"])]
    assert [s for s, _, _ in core.bridge_gaps(cues, 3)] == [0.0, 0.5, 0.65]


def test_overlap_is_cut():
    assert core.bridge_gaps([(0.0, 1.5, ["a"]), (1.2, 2.0, ["b"])], 2.0)[0][1] == 1.2


def test_off_and_last_subtitle_unchanged():
    cues = [(0.0, 1.0, ["a"]), (1.1, 2.0, ["b"])]
    assert core.bridge_gaps(cues, 0) == cues
    assert core.bridge_gaps(cues, 2.0)[-1] == (1.1, 2.0, ["b"])


def test_no_gap_left_in_the_srt_text(tmp_path):
    """In the file the end of each subtitle is exactly the start of the next one."""
    cues = core.bridge_gaps([(0.0, 0.333, ["a"]), (0.4567, 0.9, ["b"]), (0.95, 1.2, ["c"])], 2.0)
    out = tmp_path / "x.srt"
    core.write_srt(out, cues)
    times = re.findall(r"(\S+) --> (\S+)", out.read_text(encoding="utf-8"))
    assert all(end == nxt_start for (_, end), (nxt_start, _) in zip(times, times[1:]))


# ── whole pipeline: WhisperX JSON → SRT file (no GPU needed) ───────────────────

def run(tmp_path, mode, **layout):
    ini = tmp_path / "whisper_tool.ini"
    shutil.copy(ROOT / "whisper_tool.example.ini", ini)
    core.save_ini(ini, {("Layout", k): str(v) for k, v in layout.items()})
    src = tmp_path / "talk.json"
    result = make_result("Hallo zusammen. Heute zeige ich Dr. Müller das neue Werkzeug.", step=0.5, length=0.35,
                         pauses={2: 0.1, 9: 4.0})
    src.write_text(json.dumps(result), encoding="utf-8")
    cfg = core.Config(ini)
    opts = core.JobOptions.from_config(cfg)
    opts.mode = mode
    info = core.Engine(cfg, log=lambda m: None).process(src, opts)
    text = info["output"].read_text(encoding="utf-8")
    return re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", text), result


def secs(t):
    h, m, s = t.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@pytest.mark.parametrize("mode", ["sentence", "word", "raw"])
def test_pipeline_flicker_free(tmp_path, mode):
    times, _ = run(tmp_path, mode, bridge_gaps=2)
    gaps = [secs(b[0]) - secs(a[1]) for a, b in zip(times, times[1:])]
    # only the 4 s pause before "Werkzeug." stays a gap, everything else is closed exactly
    assert all(g == 0 or g >= 2 for g in gaps), gaps
    assert all(g >= 0 for g in gaps)


def test_word_mode_starts_are_the_spoken_times(tmp_path):
    times, result = run(tmp_path, "word", bridge_gaps=2)
    spoken = [w["start"] for w in result["segments"][0]["words"]]
    assert [secs(s) for s, _ in times] == pytest.approx(spoken)


def test_pipeline_off_keeps_gaps(tmp_path):
    times, _ = run(tmp_path, "word", bridge_gaps=0)
    assert any(secs(b[0]) - secs(a[1]) > 0 for a, b in zip(times, times[1:]))


def test_reading_time_gap_is_closed_too(tmp_path):
    # min. display time leaves 80 ms before the next subtitle – with bridging that gap is gone
    times, _ = run(tmp_path, "sentence", bridge_gaps=2, min_duration=3)
    gaps = [secs(b[0]) - secs(a[1]) for a, b in zip(times, times[1:])]
    assert not any(0 < g < 2 for g in gaps)


def test_off_by_default():
    assert core.Config(ROOT / "whisper_tool.example.ini").bridge_gaps == 0
