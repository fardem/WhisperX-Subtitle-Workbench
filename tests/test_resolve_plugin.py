"""
The Lua plugin, run against a fake DaVinci Resolve (tests/resolve_mock.lua).

Needs the package "lupa" (LuaJIT for Python) – without it these tests are skipped:
    venv\\Scripts\\python.exe -m pip install lupa
The fake follows the behaviour tested in Resolve Studio 21.1.2; it cannot replace a test in
Resolve itself (see the test guide in the README), but it catches mistakes in the plugin's logic.
"""

import os
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")
from lupa import luajit21  # noqa: E402

import wt_i18n as i18n  # noqa: E402
import wt_resolve as wr  # noqa: E402
from helpers import ROOT  # noqa: E402

MOCK_LUA = (Path(__file__).parent / "resolve_mock.lua").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "resolve" / wr.PLUGIN_FILE).read_text(encoding="utf-8")


class Harness:
    def __init__(self, tmp_path: Path, lang="de", template=None):
        self.wb = tmp_path / "Workbench"
        (self.wb / "venv").mkdir(parents=True, exist_ok=True)
        (self.wb / "whisper_tool.py").write_text("# fake", encoding="utf-8")
        self.python = self.wb / "venv" / "pythonw"
        self.python.write_text("", encoding="utf-8")
        self.last = self.wb / wr.TEMP_DIR.name
        self.last.mkdir(parents=True, exist_ok=True)
        self.texts = wr.plugin_texts()
        self.lang = lang
        self.code = template if template is not None else wr.build_plugin(
            TEMPLATE, self.wb, self.python, lang, self.texts, "0.5.0")
        self.lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        self.log: list[str] = []
        self.commands: list[str] = []
        self.error = None
        g = self.lua.globals()
        as_lua = self.lua.eval("function(f) return function(...) return f(...) end end")
        g.print = lambda *a: self.log.append(" ".join(str(x) for x in a))
        g.os.execute = as_lua(self._execute)        # a real Lua function: the plugin checks type()
        g.PY = self.lua.table_from({
            "read": lambda p: Path(p).read_text(encoding="utf-8") if Path(p).is_file() else None,
            "write": lambda p, t: Path(p).write_text(t, encoding="utf-8") and True,
            "exists": lambda p: Path(p).is_file(),
            "isdir": lambda p: Path(p).is_dir(),
            "mkdir": lambda p: Path(p).mkdir(parents=True, exist_ok=True) or True,
            "rmdir": self._rmdir,
            "sep": os.sep,
            "scenario": self._scenario,
        })
        self.lua.execute(MOCK_LUA)
        self.mock = g.MOCK
        self.steps = None

    def _execute(self, cmd):
        self.commands.append(cmd)
        return 0

    @staticmethod
    def _rmdir(p):
        try:
            Path(p).rmdir()
            return True
        except OSError:
            return False

    def _scenario(self):
        try:
            self.steps(self)
        except BaseException as e:            # re-raised after the plugin returned
            self.error = e

    def run(self, steps, **setup):
        self.mock.setup(_lua_tables(self.lua, setup))
        for key in ("deliver_marks",):
            if key in setup:
                self.mock[key] = _lua_tables(self.lua, setup[key])
        self.steps = steps
        self.lua.execute(self.code)
        if self.error:
            raise self.error

    # helpers used inside the steps
    def T(self, key, **kw):
        i18n.load(self.lang)
        try:
            return i18n.T(f"plugin.{key}", **kw)
        finally:
            i18n.load("en")

    def text(self, item):
        return self.mock.text(item)

    def tick(self, n=1):
        for _ in range(n):
            self.mock.tick()

    def click(self, item):
        self.mock.click(item)

    def calls(self, name):
        return [tuple(c.values())[1:] for c in self.mock.calls.values() if c[1] == name]

    def job_dirs(self):
        return [d for d in self.last.iterdir() if d.is_dir()]

    def tracks(self):
        tl = self.mock.timeline
        out = []
        for t in tl.tracks.values():
            out.append({"name": t.name, "enabled": t.enabled, "locked": t.locked,
                        "starts": [it.start for it in t["items"].values()]})
        return out

    def workbench_hands_over(self, lang="de", cues=((1.0, 2.5, "Hallo"), (4.0, 6.0, "Welt")), **extra):
        """What the Workbench does: transcribe (fake SRT next to the WAV) and hand over."""
        (job_dir,) = self.job_dirs()
        wav = job_dir / "audio.wav"
        job = wr.find_job(wav)
        assert job is not None, "the plugin's job.txt is not recognized"
        srt = job_dir / "audio.srt"
        srt.write_text("".join(f"{i}\n{_t(a)} --> {_t(b)}\n{txt}\n\n" for i, (a, b, txt) in enumerate(cues, 1)),
                       encoding="utf-8")
        (job_dir / "audio.gaps.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n\n", encoding="utf-8")
        return job, wr.hand_over(job, srt, lang, last_dir=self.last, **extra)


def _t(sec):
    ms = round(sec * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def _lua_tables(lua, value):
    """Nested dicts/lists → Lua tables (lists become 1-based)."""
    if isinstance(value, dict):
        return lua.table_from({k: _lua_tables(lua, v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return lua.table_from([_lua_tables(lua, v) for v in value])
    return value


def timeline(**kw):
    base = {"name": "Interview Müller", "id": "TL-1", "fps": 25, "start": 90000, "start_tc": "01:00:00:00",
            "last": 90000 + 25 * 600, "tracks": [], "marks": {}}
    base.update(kw)
    return base


# ─── Transcribe → hand over → import ──────────────────────────────────────────

def test_full_round_trip_with_in_out(tmp_path):
    h = Harness(tmp_path)
    marks = {"video": {"in": 250, "out": 1500}, "audio": {"in": 250, "out": 1500}}
    result = {}

    def steps(h):
        assert h.T("ready") == h.text("Status")
        assert "Interview Müller" in h.text("Info")
        h.click("Transcribe")
        assert h.mock.page == "deliver"
        h.tick()                                    # rendering 50 %
        assert "50" in h.text("Status")
        h.tick()                                    # render finished → job.txt, Workbench started
        assert h.mock.page == "edit"                # page restored
        assert len(h.commands) == 1 and "whisper_tool.py" in h.commands[0] and "audio.wav" in h.commands[0]
        assert h.T("hint_changes") in h.text("Status")
        job, res = h.workbench_hands_over()
        result["job"], result["res"] = job, res
        h.tick()                                    # done.txt → import
        result["status"] = h.text("Status")

    h.run(steps, timelines=[timeline(marks=marks)])
    job, res = result["job"], result["res"]
    assert (job.start_frame, job.in_frame, job.out_frame, job.whole) == (90000, 90250, 91500, False)
    assert res.offset == pytest.approx(10.0)       # 250 frames at 25 fps
    tracks = h.tracks()
    assert len(tracks) == 1                         # no subtitle track before → one new track
    assert tracks[0]["name"] == "WhisperX DE" and tracks[0]["enabled"]
    assert tracks[0]["starts"] == [90000 + 11 * 25, 90000 + 14 * 25]   # In point + 1 s and + 4 s
    assert result["status"].startswith(h.T("placed", n=2, track=1, name="WhisperX DE"))
    assert h.job_dirs() == []                       # WAV and job folder deleted
    assert [p.name for p in h.last.iterdir()] == ["Interview_Mueller_DE.srt"]
    assert list(h.mock.renders.values()) == []      # render job deleted
    assert all("WhisperX-Backup" not in p for p in h.mock.presets.values())
    assert (h.mock.format.format, h.mock.format.codec) == ("mov", "H264")   # Deliver settings restored
    assert h.mock.saved == 1                        # project saved before the import
    folders = [f.name for f in h.mock.root.subs.values()]
    assert folders == ["WhisperX"] and h.mock.current_folder.name == "Master"


def test_whole_timeline_and_marks_restored(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        assert h.mock.timeline.marks.video is None   # the Deliver page had set marks – removed again
        h.workbench_hands_over(cues=((0.4, 1.0, "a"),))
        h.tick()

    h.run(steps, timelines=[timeline(start=0, start_tc="00:00:00:00", last=25 * 60)],
          deliver_marks={"in": 10, "out": 20})
    assert ("SelectAllFrames", True) in h.calls("SetRenderSettings")
    assert not any(c[0] == "MarkIn" for c in h.calls("SetRenderSettings"))
    assert h.tracks()[0]["starts"] == [10]          # 0.4 s × 25 fps


def test_29_97_drop_frame_one_hour_start(tmp_path):
    h = Harness(tmp_path)
    start = 107892                                  # 01:00:00;00 drop frame
    marks = {"audio": {"in": 300, "out": 9000}}
    out = {}

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        out["job"], out["res"] = h.workbench_hands_over(cues=((1.0, 2.0, "x"),))
        h.tick()

    h.run(steps, timelines=[timeline(fps=29.97, drop="1", start=start, start_tc="01:00:00;00",
                                     last=start + 20000, marks=marks)])
    assert out["res"].offset == pytest.approx(300 * 1001 / 30000, abs=0.0005)
    # SRT time 11.01 s → frame start + round(11.01 × 29.97) = start + 330
    assert h.tracks()[0]["starts"] == [start + 330]
    assert not any("position" in line.lower() and "warn" in line.lower() for line in h.log)


def test_first_empty_track_is_used_and_others_deactivated(tmp_path):
    h = Harness(tmp_path)
    tracks = [{"name": "Original", "enabled": True, "items": [{"start": 10, "stop": 20, "text": "x"}]},
              {"name": "Subtitle 2", "enabled": False}]

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        h.workbench_hands_over()
        h.tick()

    h.run(steps, timelines=[timeline(tracks=tracks)])
    t = h.tracks()
    assert len(t) == 2 and t[1]["name"] == "WhisperX DE" and t[1]["enabled"] and not t[0]["enabled"]
    assert t[0]["starts"] == [10]


def test_locked_empty_track_is_skipped(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        h.workbench_hands_over()
        h.tick()

    h.run(steps, timelines=[timeline(tracks=[{"name": "Locked", "locked": True}])])
    t = h.tracks()
    assert t[0]["starts"] == [] and t[1]["name"] == "WhisperX DE" and len(t[1]["starts"]) == 2


def test_end_frame_changed_asks_first(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        h.mock.timeline.last = h.mock.timeline.last + 100
        h.workbench_hands_over()
        h.tick()
        seen["question"] = h.text("Status")
        seen["hidden"] = h.mock["items"].Yes.Hidden
        assert h.tracks() == []                     # nothing imported before the answer
        h.click("Yes")

    h.run(steps, timelines=[timeline()])
    assert not seen["hidden"] and "→" in seen["question"]
    assert h.tracks()[0]["name"] == "WhisperX DE"


def test_end_frame_changed_and_no_keeps_the_srt(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        h.mock.timeline.last = h.mock.timeline.last - 10
        h.workbench_hands_over()
        h.tick()
        h.click("No")

    h.run(steps, timelines=[timeline()])
    assert h.tracks() == [] and h.job_dirs() == []
    assert len(list(h.last.iterdir())) == 1


def test_timeline_switched_back_by_unique_id(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        h.mock.timeline = h.mock.timelines[2]       # the user opened another timeline meanwhile
        h.workbench_hands_over()
        h.tick()

    h.run(steps, timelines=[timeline(), timeline(name="Other", id="TL-2")])
    assert h.mock.timeline.id == "TL-1"
    assert h.calls("SetCurrentTimeline")
    assert h.tracks()[0]["name"] == "WhisperX DE"


def test_timeline_gone_keeps_srt(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        _, res = h.workbench_hands_over()
        h.mock.timelines[1].api = None              # "deleted"
        h.mock.timelines[1].id = "gone"
        h.tick()
        seen["status"] = h.text("Status")
        seen["srt"] = res.srt

    h.run(steps, timelines=[timeline()])
    assert seen["status"] == h.T("timeline_gone", name="Interview Müller", path=seen["srt"])
    assert seen["srt"].is_file() and h.job_dirs() == []


def test_cancel_while_waiting_removes_the_job(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        assert len(h.job_dirs()) == 1
        h.click("Cancel")
        assert h.text("Status") == h.T("cancelled")

    h.run(steps, timelines=[timeline()])
    assert h.job_dirs() == [] and h.tracks() == []


def test_closing_while_rendering_stops_and_restores(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        h.click("Transcribe")
        h.mock.close_window()

    h.run(steps, timelines=[timeline(marks={"video": {"in": 5, "out": 50}, "audio": {"in": 5, "out": 50}})])
    assert h.calls("StopRendering") and h.mock.exited
    assert list(h.mock.renders.values()) == [] and h.mock.page == "edit"
    assert h.mock.timeline.marks.video["in"] == 5
    assert h.job_dirs() == []


def test_render_error_restores_everything(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.mock.render_error = "Disk full"
        h.click("Transcribe")
        h.tick(2)
        seen["status"] = h.text("Status")

    h.run(steps, timelines=[timeline()])
    assert seen["status"] == h.T("render_failed", why="Disk full")
    assert list(h.mock.renders.values()) == [] and h.mock.page == "edit" and h.job_dirs() == []
    assert (h.mock.format.format, h.mock.format.codec) == ("mov", "H264")
    assert h.commands == []


def test_start_rendering_is_retried_once(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        h.mock.refuse_start = 1
        h.click("Transcribe")
        h.tick(2)

    h.run(steps, timelines=[timeline()])
    assert len(h.calls("StartRendering")) == 2 and len(h.commands) == 1


def test_no_wav_format_is_an_error(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.mock.no_wav = True
        h.click("Transcribe")
        seen["status"] = h.text("Status")

    h.run(steps, timelines=[timeline()])
    assert seen["status"] == h.T("render_failed", why=h.T("err_wav"))
    assert h.mock.page == "edit" and h.job_dirs() == []


def test_position_warning_names_the_start_timecode(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.mock.placement_shift = -90000             # as if Resolve ignored the timeline start
        h.click("Transcribe")
        h.tick(2)
        h.workbench_hands_over()
        h.tick()
        seen["status"] = h.text("Status")

    h.run(steps, timelines=[timeline()])
    assert h.T("position_start_tc") in seen["status"]


def test_playback_running_blocks(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.mock.playing = True
        h.click("Transcribe")
        seen["status"] = h.text("Status")

    h.run(steps, timelines=[timeline()])
    assert seen["status"] == h.T("playing") and h.mock.page == "edit" and h.job_dirs() == []


def test_left_over_jobs_and_presets_of_the_plugin_are_removed(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        old = h.wb / wr.TEMP_DIR.name / "old"
        h.mock.renders = _lua_tables(h.lua, [
            {"id": "foreign", "settings": {"TargetDir": "D:/Renders"}, "name": "film.mov", "pct": 0,
             "status": "Ready", "error": "", "ticks_left": 0},
            {"id": "ours", "settings": {"TargetDir": str(old)}, "name": "audio.wav", "pct": 0,
             "status": "Ready", "error": "", "ticks_left": 0}])
        h.mock.presets = _lua_tables(h.lua, ["Default", "WhisperX-Backup-old"])
        h.click("Transcribe")
        h.tick(2)

    h.run(steps, timelines=[timeline()])
    assert [r.id for r in h.mock.renders.values()] == ["foreign"]
    assert list(h.mock.presets.values()) == ["Default"]


# ─── Import SRT … ─────────────────────────────────────────────────────────────

def test_import_srt_with_language_in_the_name(tmp_path):
    h = Harness(tmp_path)
    srt = h.last / "Interview_EN.srt"
    srt.write_text("1\n00:00:02,000 --> 00:00:03,000\nHello\n\n", encoding="utf-8")

    def steps(h):
        h.mock.request_file = str(srt)
        h.click("ImportSrt")
        h.click("ImportSrt")                       # a second import: name exists → "WhisperX EN 2"

    h.run(steps, timelines=[timeline(start=0, start_tc="00:00:00:00")])
    t = h.tracks()
    assert [x["name"] for x in t] == ["WhisperX EN", "WhisperX EN 2"]
    assert t[0]["starts"] == [50] and t[1]["starts"] == [50] and t[1]["enabled"] and not t[0]["enabled"]
    assert h.calls("RequestFile")[0][0].startswith(str(h.last))


def test_import_srt_asks_for_the_language(tmp_path):
    h = Harness(tmp_path)
    srt = tmp_path / "translation.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n\n", encoding="utf-8")

    def steps(h):
        h.mock.request_file = str(srt)
        h.click("ImportSrt")
        assert not h.mock["items"].Answer.Hidden
        h.mock["items"].Answer.Text = "tr"
        h.click("Yes")

    h.run(steps, timelines=[timeline()])
    assert h.tracks()[0]["name"] == "WhisperX TR"


def test_template_without_configuration_says_so(tmp_path):
    h = Harness(tmp_path, template=TEMPLATE)
    seen = {}

    def steps(h):
        seen["status"] = h.text("Status")
        seen["enabled"] = h.mock["items"].Transcribe.Enabled

    h.run(steps, timelines=[timeline()])
    assert seen["status"] == "not_installed" or "WhisperX" in seen["status"]
    assert seen["enabled"] is False


def test_lua_helpers(tmp_path):
    h = Harness(tmp_path)
    g = h.lua.globals()
    g.WX_TEST = True
    m = h.lua.execute(h.code)
    assert m.lang_from_name("C:/x/Interview_Mueller_DE.srt") == "DE"
    assert m.lang_from_name("talk_en.srt") == "EN"
    assert m.lang_from_name("Final_Cut.srt") is None
    assert m.clean_lang(" de-ch ") == "DE-CH"
    assert m.parse_fps("29.97 DF") == pytest.approx(29.97)
    assert m.exact_fps(29.97) == pytest.approx(30000 / 1001)
    assert m.fmt_dur(3725) == "1:02:05"
    kv = m.parse_kv("\ufeff# comment\nsrt = C:\\a b\\x.srt\nLANG=DE\n\nbroken line\n")
    assert (kv.srt, kv.lang) == ("C:\\a b\\x.srt", "DE")


def test_import_waits_while_the_timeline_plays(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        h.workbench_hands_over()
        h.mock.playing = True
        h.tick()
        seen["status"], seen["tracks"] = h.text("Status"), h.tracks()
        h.mock.playing = False
        h.tick()

    h.run(steps, timelines=[timeline()])
    assert seen["status"] == h.T("playing") and seen["tracks"] == []
    assert h.tracks()[0]["name"] == "WhisperX DE"


def test_window_comes_to_the_front_with_ok_after_the_import(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        seen["close_waiting"] = h.text("Close")
        h.workbench_hands_over()
        h.tick()
        seen["close_done"] = h.text("Close")
        h.click("Transcribe")                       # a new job: the button is "Close" again
        seen["close_new"] = h.text("Close")
        h.click("Cancel")

    h.run(steps, timelines=[timeline()])
    assert seen["close_waiting"] == h.T("btn_close") and seen["close_done"] == h.T("btn_ok")
    assert seen["close_new"] == h.T("btn_close")
    assert h.calls("Raise") and h.calls("ActivateWindow")


def test_layout_is_calculated_again_after_showing(tmp_path):
    h = Harness(tmp_path)

    def steps(h):
        n = len(h.calls("RecalcLayout"))
        h.tick()                                    # the second pass after the event loop drew the window
        assert len(h.calls("RecalcLayout")) == n + 1 >= 2

    h.run(steps, timelines=[timeline()])


def test_progress_of_the_workbench_is_shown_while_waiting(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        seen["plain"] = h.text("Status")
        (job_dir,) = h.job_dirs()
        wr.write_progress(wr.find_job(job_dir / "audio.wav"), "running", stage="asr", pct=45)
        h.tick()
        seen["asr"] = h.text("Status")
        wr.write_progress(wr.find_job(job_dir / "audio.wav"), "countdown", n=7)
        h.tick()
        seen["countdown"] = h.text("Status")
        h.click("Cancel")

    h.run(steps, timelines=[timeline()])
    assert h.T("waiting_do") in seen["plain"] and h.T("hint_changes") in seen["plain"]
    assert h.T("wb_asr", pct=45) in seen["asr"] and h.T("wb_countdown", n=7) in seen["countdown"]


def test_gaps_and_fast_subtitles_get_own_tracks_and_the_transcript_stays_active(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        h.click("Transcribe")
        h.tick(2)
        h.workbench_hands_over(cues=((1.0, 2.5, "Hallo"), (4.0, 4.5, "Viel zu viel Text für so wenig Zeit")),
                               gaps=[(8.0, 9.5, -4.0)], fast_cps=17)
        h.tick()
        seen["status"] = h.text("Status")

    h.run(steps, timelines=[timeline()])
    t = h.tracks()
    assert [x["name"] for x in t] == ["WhisperX DE", h.T("track_gaps"), h.T("track_fast")]
    assert [x["enabled"] for x in t] == [True, False, False]
    assert t[1]["starts"] == [90000 + 8 * 25] and t[2]["starts"] == [90000 + 4 * 25]      # no In point: no offset
    assert h.T("placed_extra", n=1, track=2, name=h.T("track_gaps")) in seen["status"]
    assert h.job_dirs() == []                           # the extra SRTs are deleted with the job folder


def test_language_and_mode_chosen_in_the_plugin_go_into_the_job(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def steps(h):
        assert list(h.mock["items"].Lang.entries.values())[0] == h.T("opt_workbench")
        h.mock.choose("Lang", 4)                       # Turkish
        h.mock.choose("Mode", 2)                       # word by word
        h.click("Transcribe")
        h.tick(2)
        (job_dir,) = h.job_dirs()
        seen["job"] = wr.find_job(job_dir / "audio.wav")
        h.click("Cancel")

    h.run(steps, timelines=[timeline()])
    assert (seen["job"].language, seen["job"].mode) == ("tr", "word")


def test_window_position_and_choices_are_remembered(tmp_path):
    h = Harness(tmp_path)

    def first(h):
        h.mock.choose("Mode", 3)                       # raw
        h.mock.move_to = None
        h.mock["items"].WhisperXWin.Geometry = h.lua.table_from([300, 200, 700, 500])
        h.mock.close_window()

    h.run(first, timelines=[timeline()])
    saved = wr.read_kv(h.last / "plugin.txt")
    assert (saved["mode"], saved["geometry"]) == ("raw", "300,200,700,500")

    h2 = Harness(tmp_path)
    seen = {}

    def second(h):
        seen["geometry"] = list(h.mock.window_spec.Geometry.values())
        seen["mode"] = h.mock["items"].Mode.CurrentIndex

    h2.run(second, timelines=[timeline()])
    assert seen == {"geometry": [300, 200, 700, 500], "mode": 3}


def test_unusable_saved_geometry_is_ignored(tmp_path):
    h = Harness(tmp_path)
    g = h.lua.globals()
    g.WX_TEST = True
    m = h.lua.execute(h.code)
    for bad in ("", "1,2,3", "0,0,100,100", "-5000,0,700,500", "0,0,99999,500"):
        assert m.saved_geometry(h.lua.table_from({"geometry": bad})) is None
    assert list(m.saved_geometry(h.lua.table_from({"geometry": "10,20,640,460"})).values()) == [10, 20, 640, 460]
