"""DaVinci Resolve connection (wt_resolve): job and done files, Resolve mode, SRT shift, "only one last SRT"."""

import os
import re
import time

import pytest
from pathlib import Path

import wt_core as core
import wt_i18n as i18n
import wt_resolve as wr
from helpers import ROOT

TEMPLATE = (ROOT / "resolve" / wr.PLUGIN_FILE).read_text(encoding="utf-8")


def make_job(folder, **kw) -> wr.ResolveJob:
    """A job folder as the plugin leaves it: audio.wav and job.txt."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio.wav").write_bytes(b"RIFF....WAVE")
    values = dict(folder=folder, audio="audio.wav", job_id="20261105-101500-0042", timeline="Interview Müller",
                  timeline_id="TL-1", fps=25.0, fps_text="25", start_frame=90000, start_tc="01:00:00:00",
                  in_frame=90000, out_frame=104999, end_frame=105000, whole=True)
    values.update(kw)
    job = wr.ResolveJob(**values)
    wr.write_kv(folder / wr.JOB_FILE, job.values(), "WhisperX Subtitle Workbench - DaVinci Resolve job")
    return job


def srt(*cues) -> str:
    return "".join(f"{i}\n{a} --> {b}\n{t}\n\n" for i, (a, b, t) in enumerate(cues, 1))


# ─── SRT shift: frames → time ─────────────────────────────────────────────────

def test_exact_ntsc_rates():
    assert wr.exact_fps(29.97) == pytest.approx(30000 / 1001)
    assert wr.exact_fps("23.976") == pytest.approx(24000 / 1001)
    assert wr.exact_fps("59.94 DF") == pytest.approx(60000 / 1001)
    assert wr.exact_fps(25) == 25 and wr.exact_fps("24") == 24 and wr.exact_fps(50) == 50
    with pytest.raises(ValueError):
        wr.parse_fps("abc")


def test_offset_25_fps_in_point():
    job = wr.ResolveJob(folder=ROOT, audio="a.wav", fps=25, start_frame=90000, in_frame=90000 + 250)
    assert wr.srt_offset_seconds(job) == pytest.approx(10.0)


def test_offset_29_97_drop_frame():
    # 01:00:00;00 drop frame = frame 107892; the In point 300 frames later = 300 × 1001/30000 s
    job = wr.ResolveJob(folder=ROOT, audio="a.wav", fps=29.97, drop_frame=True, start_frame=107892,
                        in_frame=107892 + 300)
    assert wr.srt_offset_seconds(job) == pytest.approx(10.01)


def test_whole_timeline_has_no_offset_whatever_the_start():
    for start in (0, 86400, 90000, 107892, 900000, 89687):       # 00:00, 01:00 (24/25/29.97), 10:00, 00:59:47:12
        job = wr.ResolveJob(folder=ROOT, audio="a.wav", fps=25, start_frame=start, in_frame=start)
        assert wr.srt_offset_seconds(job) == 0


def test_start_timecode_only_with_the_switch(monkeypatch):
    job = wr.ResolveJob(folder=ROOT, audio="a.wav", fps=25, start_frame=90000, in_frame=90250)
    monkeypatch.setattr(wr, "INCLUDE_START_TC", True)
    assert wr.srt_offset_seconds(job) == pytest.approx(3610.0)


def test_shift_changes_only_the_times():
    text = ("\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\n<i>Hallo</i> 00:00:09,000\r\n\r\n"
            "2\r\n00:00:04,000 --> 00:00:06,000\r\nWelt -->\r\n\r\n")
    out, cues, first = wr.shift_srt(text, 10.01)
    assert cues == 2 and first == pytest.approx(11.01)
    assert out.splitlines() == ["\ufeff1", "00:00:11,010 --> 00:00:13,510", "<i>Hallo</i> 00:00:09,000", "",
                                "2", "00:00:14,010 --> 00:00:16,010", "Welt -->", ""]
    assert out.count("\r\n") == text.count("\r\n")                # line endings kept


@pytest.mark.parametrize("fps, frames", [(25, 250), (29.97, 300), (29.97, 1), (23.976, 1439), (25, 89999)])
def test_shift_lands_on_the_frame(fps, frames):
    """In point offset in frames → seconds → back to frames gives exactly the same frame."""
    job = wr.ResolveJob(folder=ROOT, audio="a.wav", fps=fps, start_frame=0, in_frame=frames)
    _, _, first = wr.shift_srt(srt(("00:00:00,000", "00:00:01,000", "x")), wr.srt_offset_seconds(job))
    assert round(first * wr.exact_fps(fps)) == frames


def test_shift_beyond_one_hour_and_odd_formats():
    out, cues, _ = wr.shift_srt("1\n0:59:59.5 --> 1:00:00,25\nx\n", 3600)
    assert cues == 1 and "01:59:59,500 --> 02:00:00,250" in out


def test_ascii_names():
    assert wr.ascii_name("Gülşen Ağaoğlu – Röportaj 1") == "Guelsen_Agaoglu_Roeportaj_1"
    assert wr.ascii_name("İstanbul ıslak ÇAY") == "Istanbul_islak_CAY"
    assert wr.ascii_name("Straße/Teil:2?") == "Strasse_Teil_2"
    assert wr.ascii_name("لقاء") == "Timeline" and wr.ascii_name("CON") == "CON_"
    assert wr.last_srt_name("Interview Müller", "de") == "Interview_Mueller_DE.srt"
    assert wr.lang_code("") == "XX" and wr.lang_code("-") == "XX"


# ─── job and done files (key=value) ───────────────────────────────────────────

def test_job_file_round_trip(tmp_path):
    job = make_job(tmp_path / "job", timeline="Gespräch = Teil 1 # Probe", whole=False, in_frame=90250,
                   out_frame=95000, fps=29.97, fps_text="29.97", drop_frame=True)
    back = wr.find_job(tmp_path / "job" / "audio.wav")
    assert back is not None
    assert (back.timeline, back.in_frame, back.out_frame, back.whole, back.drop_frame) == \
        ("Gespräch = Teil 1 # Probe", 90250, 95000, False, True)
    assert back.fps == pytest.approx(29.97) and back.start_frame == 90000 and back.job_id == job.job_id


def test_job_file_as_the_plugin_writes_it(tmp_path):
    # Lua writes CRLF-free "key=value" with a comment line and extra keys the Workbench ignores
    (tmp_path / "audio.wav").write_bytes(b"x")
    (tmp_path / "job.txt").write_bytes(
        "# WhisperX Subtitle Workbench - DaVinci Resolve job\n"
        "format=whisperx-resolve-job/1\nplugin=0.5.0\njob=20261105-1\naudio=audio.wav\n"
        "timeline=Röportaj\ntimeline_id=abc\nproject=P\nfps=29.97\ndrop_frame=1\nstart_frame=107892\n"
        "start_tc=01:00:00;00\nrange=inout\nin_frame=108000\nout_frame=110000\nend_frame=200000\n"
        "resolve=21.1.2.10\ncreated=2026-11-05 10:15:00\n".encode("utf-8"))
    job = wr.find_job(tmp_path / "audio.wav")
    assert job.timeline == "Röportaj" and job.in_frame == 108000 and not job.whole
    assert wr.srt_offset_seconds(job) == pytest.approx(108 * 1001 / 30000)


def test_kv_parser_is_tolerant():
    v = wr.parse_kv("\ufeff# comment\r\n; other comment\r\n KEY = a = b \r\n\r\nno equals sign\r\nempty=\r\n")
    assert v == {"key": "a = b", "empty": ""}


def test_done_file_is_written_atomically(tmp_path):
    path = tmp_path / "done.txt"
    wr.write_kv(path, {"srt": r"C:\WhisperX\resolve_temp\A_DE.srt", "lang": "DE", "note": "two\nlines"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["done.txt"]          # no .tmp left over
    assert wr.read_kv(path) == {"srt": r"C:\WhisperX\resolve_temp\A_DE.srt", "lang": "DE",
                                "note": "two lines"}


# ─── Resolve mode ─────────────────────────────────────────────────────────────

def test_resolve_mode_only_with_the_plugins_job_file(tmp_path):
    make_job(tmp_path / "job")
    assert wr.find_job(tmp_path / "job" / "audio.wav") is not None
    other = tmp_path / "job" / "other.wav"
    other.write_bytes(b"x")
    assert wr.find_job(other) is None                       # the job file belongs to audio.wav only
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "talk.mp4").write_bytes(b"x")
    assert wr.find_job(plain / "talk.mp4") is None          # no job file → exactly as before
    (plain / "job.txt").write_text("audio=talk.mp4\nfps=25\nstart_frame=0\n", encoding="utf-8")
    assert wr.find_job(plain / "talk.mp4") is None          # someone else's job.txt


def test_broken_job_file_is_no_resolve_job(tmp_path):
    make_job(tmp_path / "job")
    jf = tmp_path / "job" / "job.txt"
    jf.write_text(jf.read_text(encoding="utf-8").replace("fps=25", "fps=fast"), encoding="utf-8")
    assert wr.find_job(tmp_path / "job" / "audio.wav") is None


def test_job_alive_until_cancelled(tmp_path):
    job = make_job(tmp_path / "job")
    assert job.alive()
    (tmp_path / "job" / wr.CANCEL_FILE).write_text("x", encoding="utf-8")
    assert not job.alive()


# ─── hand over and "only one last SRT" ────────────────────────────────────────

def test_hand_over_writes_shifted_srt_and_done_file(tmp_path):
    job = make_job(tmp_path / "job", whole=False, in_frame=90250)
    src = tmp_path / "job" / "audio.srt"
    src.write_text(srt(("00:00:01,000", "00:00:02,000", "Hallo"), ("00:00:03,000", "00:00:04,000", "Welt")),
                   encoding="utf-8")
    (tmp_path / "job" / "audio.gaps.srt").write_text("", encoding="utf-8")
    last = tmp_path / "letzte"
    res = wr.hand_over(job, src, "de", last_dir=last)
    assert res.srt == last / "Interview_Mueller_DE.srt" and res.lang == "DE" and res.cues == 2
    assert res.first == pytest.approx(11.0) and res.offset == pytest.approx(10.0)
    assert "00:00:11,000 --> 00:00:12,000" in res.srt.read_text(encoding="utf-8")
    assert src.read_text(encoding="utf-8").startswith("1\n00:00:01,000")         # the original stays
    done = wr.read_kv(job.done_path)
    assert done["srt"] == str(res.srt) and done["lang"] == "DE" and done["first"] == "11.000"
    assert done["files"] == "audio.gaps.srt;audio.srt"


def test_only_one_last_srt(tmp_path):
    last = tmp_path / "letzte"
    a = make_job(tmp_path / "a", timeline="Erste Timeline")
    b = make_job(tmp_path / "b", timeline="Zweite")
    for job in (a, b):
        (job.folder / "audio.srt").write_text(srt(("00:00:01,000", "00:00:02,000", "x")), encoding="utf-8")
    wr.hand_over(a, a.folder / "audio.srt", "de", last_dir=last)
    assert [p.name for p in last.iterdir()] == ["Erste_Timeline_DE.srt"]
    wr.hand_over(b, b.folder / "audio.srt", "en", last_dir=last)
    assert [p.name for p in last.iterdir()] == ["Zweite_EN.srt"]
    wr.hand_over(b, b.folder / "audio.srt", "en", last_dir=last)                  # same name: replaced
    assert [p.name for p in last.iterdir()] == ["Zweite_EN.srt"]


def test_previous_srt_stays_when_writing_fails(tmp_path, monkeypatch):
    last = tmp_path / "letzte"
    wr.write_last_srt(last, "Alt_DE.srt", "old")

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(wr.os, "replace", broken)
    with pytest.raises(OSError):
        wr.write_last_srt(last, "Neu_DE.srt", "new")
    assert [p.name for p in last.iterdir()] == ["Alt_DE.srt"]


def test_cancelled_job_leaves_the_last_srt_alone(tmp_path):
    last = tmp_path / "letzte"
    wr.write_last_srt(last, "Alt_DE.srt", "old")
    job = make_job(tmp_path / "job")
    (job.folder / "audio.srt").write_text(srt(("00:00:01,000", "00:00:02,000", "x")), encoding="utf-8")
    (job.folder / wr.CANCEL_FILE).write_text("x", encoding="utf-8")
    with pytest.raises(wr.ResolveError):
        wr.hand_over(job, job.folder / "audio.srt", "de", last_dir=last)
    assert [p.name for p in last.iterdir()] == ["Alt_DE.srt"] and not job.done_path.exists()


def test_hand_over_without_subtitles(tmp_path):
    job = make_job(tmp_path / "job")
    (job.folder / "audio.srt").write_text("", encoding="utf-8")
    with pytest.raises(wr.ResolveError):
        wr.hand_over(job, job.folder / "audio.srt", "de", last_dir=tmp_path / "letzte")
    with pytest.raises(wr.ResolveError):
        wr.hand_over(job, job.folder / "missing.srt", "de", last_dir=tmp_path / "letzte")


def test_last_srt_lies_next_to_the_job_folders(tmp_path):
    root = tmp_path / "resolve_temp"
    job = make_job(root / "20261105-1")                        # a job that is still waiting
    (root / "notes.txt").write_text("x", encoding="utf-8")
    wr.write_last_srt(root, "Erste_DE.srt", "a")
    wr.write_last_srt(root, "Zweite_DE.srt", "b")
    assert sorted(p.name for p in root.iterdir()) == ["20261105-1", "Zweite_DE.srt", "notes.txt"]
    assert job.alive()


def test_folder_of_version_0_5_0_is_removed(tmp_path):
    legacy = tmp_path / "temp" / "resolve"
    make_job(legacy / "job")
    wr.write_last_srt(legacy / "letzte", "A_DE.srt", "x")
    assert wr.remove_legacy_dir(legacy) and not (tmp_path / "temp").exists()
    legacy.mkdir(parents=True)
    (tmp_path / "temp" / "mine.txt").write_text("x", encoding="utf-8")   # temp/ holds something else
    assert wr.remove_legacy_dir(legacy) and (tmp_path / "temp" / "mine.txt").is_file()
    assert wr.remove_legacy_dir(legacy) is False


def test_cleanup_removes_only_finished_or_old_jobs(tmp_path):
    root = tmp_path / "resolve"
    make_job(root / "active")
    make_job(root / "cancelled")
    (root / "cancelled" / wr.CANCEL_FILE).write_text("x", encoding="utf-8")
    make_job(root / "imported")
    (root / "imported" / wr.IMPORTED_FILE).write_text("x", encoding="utf-8")
    (root / "empty").mkdir()
    make_job(root / "old")
    old = time.time() - 4 * 86400
    for p in (root / "old").iterdir():
        os.utime(p, (old, old))
    wr.write_last_srt(root, "A_DE.srt", "x")
    assert wr.cleanup_jobs(root) == 4
    assert sorted(p.name for p in root.iterdir()) == ["A_DE.srt", "active"]


# ─── plugin installation ──────────────────────────────────────────────────────

def lua_keys() -> set[str]:
    return set(re.findall(r'\bT\("([a-z_]+)"', TEMPLATE)) | set(re.findall(r'"(range_all|range_inout)"', TEMPLATE))


@pytest.mark.parametrize("code", [c for c, _ in i18n.available()])
def test_every_plugin_text_exists(code):
    texts = wr.plugin_texts()[code]
    assert sorted(lua_keys() - set(texts)) == []


def test_no_unused_plugin_texts():
    assert sorted(set(wr.plugin_texts()["en"]) - lua_keys()) == []


def test_build_plugin_writes_path_language_and_texts():
    texts = {"en": {"ready": 'Say "hi"\nnow'}, "de": {"ready": "Grüß dich \\ C:\\x"}}
    lua = wr.build_plugin(TEMPLATE, r"C:\WhisperX Bench", r"C:\WhisperX Bench\venv\Scripts\pythonw.exe", "de",
                          texts, "0.5.0")
    assert 'workbench = "C:\\\\WhisperX Bench",' in lua
    assert '["ready"] = "Say \\"hi\\"\\nnow",' in lua and '["ready"] = "Grüß dich \\\\ C:\\\\x",' in lua
    assert 'lang      = "de",' in lua
    assert lua.count(wr.CONFIG_BEGIN) == 1 and lua.count(wr.CONFIG_END) == 1
    assert lua.endswith(TEMPLATE[TEMPLATE.index(wr.CONFIG_END) + len(wr.CONFIG_END):])


def test_install_and_detect_plugin(tmp_path):
    wb = tmp_path / "Workbench"
    (wb / "venv").mkdir(parents=True)
    py = wb / "venv" / "pythonw.exe"
    py.write_text("", encoding="utf-8")
    target = tmp_path / "Scripts" / "Utility"
    assert wr.installed_plugin(target / wr.PLUGIN_FILE) is None
    path = wr.install_plugin("0.5.0", "tr", target_dir=target, workbench=wb, python=py)
    info = wr.installed_plugin(path)
    assert (info["version"], info["workbench"], info["lang"]) == ("0.5.0", str(wb), "tr")
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")                # Lua cannot read a BOM
    assert (wb / "resolve_temp").is_dir()
    assert "Yazıya dök" in path.read_text(encoding="utf-8")                  # all languages are in it


def test_install_refuses_non_ascii_paths(tmp_path):
    wb = tmp_path / "Ünterlagen"
    wb.mkdir()
    py = wb / "pythonw.exe"
    py.write_text("", encoding="utf-8")
    with pytest.raises(wr.ResolveError) as e:
        wr.install_plugin("0.5.0", "de", target_dir=tmp_path / "s", workbench=wb, python=py)
    assert "Ünterlagen" in str(e.value) and not (tmp_path / "s").exists()


def test_template_is_not_configured():
    info_block = TEMPLATE[TEMPLATE.index(wr.CONFIG_BEGIN):TEMPLATE.index(wr.CONFIG_END)]
    assert "workbench = nil" in info_block and "texts     = {}" in info_block


def test_scripts_folder_is_utility():
    assert wr.scripts_dir().name == "Utility" and wr.scripts_dir().parent.name == "Scripts"


def test_plugin_python_prefers_pythonw(tmp_path):
    (tmp_path / "python.exe").write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    assert wr.plugin_python(tmp_path / "python.exe").name == "pythonw.exe"


# ─── settings ─────────────────────────────────────────────────────────────────

def test_ini_resolve_section(tmp_path):
    cfg = core.Config(ROOT / "whisper_tool.example.ini")
    assert cfg.resolve_countdown == 10 and cfg.resolve_last == ""
    ini = tmp_path / "old.ini"
    ini.write_text("[Menu]\ntimeout = 15\n", encoding="utf-8")                # 0.4.x INI without [Resolve]
    assert core.Config(ini).resolve_countdown == 10
    ini.write_text("[Resolve]\ncountdown = -3\n", encoding="utf-8")
    assert core.Config(ini).resolve_countdown == 0


def test_language_and_mode_of_the_plugin(tmp_path):
    make_job(tmp_path / "a", language="tr", mode="word")
    make_job(tmp_path / "b")
    a, b = wr.find_job(tmp_path / "a" / "audio.wav"), wr.find_job(tmp_path / "b" / "audio.wav")
    assert (a.language, a.mode) == ("tr", "word") and (b.language, b.mode) == ("", "")


def test_progress_file(tmp_path):
    job = make_job(tmp_path / "job")
    assert wr.write_progress(job, "running", stage="asr", pct=45)
    assert wr.read_kv(job.folder / wr.PROGRESS_FILE) == {"state": "running", "stage": "asr", "pct": "45"}
    for p in job.folder.iterdir():
        p.unlink()
    job.folder.rmdir()
    assert wr.write_progress(job, "done") is False               # the plugin removed the job meanwhile


def test_read_srt_cues():
    cues = wr.read_srt_cues(srt(("00:00:01,000", "00:00:02,500", "Hallo"), ("00:00:03,000", "00:00:04,000", "a\nb")))
    assert cues == [(1.0, 2.5, ["Hallo"]), (3.0, 4.0, ["a", "b"])]


def test_hand_over_with_gap_and_fast_tracks(tmp_path):
    job = make_job(tmp_path / "job", whole=False, in_frame=90250)                    # offset 10 s
    src = job.folder / "audio.srt"
    src.write_text(srt(("00:00:01,000", "00:00:03,000", "Hallo"),
                       ("00:00:04,000", "00:00:04,500", "Viel zu viel Text für so wenig Zeit")), encoding="utf-8")
    res = wr.hand_over(job, src, "de", last_dir=tmp_path / "out", gaps=[(5.0, 7.0, -3.0)],
                       gap_label=lambda s, e, rel: f"gap {e - s:.1f}", fast_cps=17,
                       fast_label=lambda cps: f"{cps:.0f} cps")
    done = wr.read_kv(job.done_path)
    gaps, fast = Path(done["gaps"]), Path(done["fast"])
    assert gaps.parent == job.folder and gaps.name == "Interview_Mueller_DE_gaps.srt"
    assert gaps.read_text(encoding="utf-8") == "1\n00:00:15,000 --> 00:00:17,000\ngap 2.0\n\n"
    assert fast.read_text(encoding="utf-8") == "1\n00:00:14,000 --> 00:00:14,500\n70 cps\n\n"
    assert (res.gaps, res.fast, done["gaps_n"], done["fast_n"]) == (1, 1, "1", "1")
    assert gaps.name in done["files"] and fast.name in done["files"]
    plain = make_job(tmp_path / "plain")
    (plain.folder / "audio.srt").write_text(srt(("00:00:01,000", "00:00:01,200", "zu schnell hier")), encoding="utf-8")
    wr.hand_over(plain, plain.folder / "audio.srt", "de", last_dir=tmp_path / "out")
    assert "gaps" not in wr.read_kv(plain.done_path) and "fast" not in wr.read_kv(plain.done_path)
