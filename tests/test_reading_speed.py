"""Reading speed: subtitles with too much text for their time are listed in the log (no setting)."""

import wt_core as core


def test_cue_chars_counts_what_is_read():
    assert core.cue_chars(["abc", "de"]) == 5          # line breaks do not count


def test_too_fast_is_reported():
    fast = core.fast_cues([(0.0, 1.0, ["x" * 40]), (2.0, 5.0, ["Weiter."])])
    assert len(fast) == 1 and fast[0][0] == 0.0 and fast[0][2] > core.READING_CPS


def test_readable_is_not_reported():
    assert core.fast_cues([(0.0, 3.0, ["x" * 40])]) == []


def test_engine_warns_about_fast_subtitles(tmp_path):
    ini = tmp_path / "t.ini"
    ini.write_text("[Layout]\n", encoding="utf-8")
    log = []
    engine = core.Engine(core.Config(ini), log=log.append)
    assert engine._report_fast([(0.0, 1.0, ["x" * 40]), (1.05, 3.0, ["ok"])]) == 1
    assert any("faster than 17 characters per second" in line for line in log)
    assert any("0:00.0 – 0:01.0" in line for line in log)
