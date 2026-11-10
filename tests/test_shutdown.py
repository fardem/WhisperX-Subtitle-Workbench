"""Shut down when done: when the PC shuts down and with which command."""

import pytest

import wt_i18n as i18n

wt = pytest.importorskip("whisper_tool")      # the GUI module – Tkinter itself is not needed for these tests


@pytest.mark.parametrize("checked, cancelled, resolve_job, expected", [
    (True, False, False, True),
    (False, False, False, False),
    (True, True, False, False),
    (True, False, True, False),
])
def test_shutdown_only_after_a_complete_run_without_resolve_job(checked, cancelled, resolve_job, expected):
    assert wt.shutdown_after_run(checked, cancelled, resolve_job) is expected


def test_windows_command_does_not_force_programs_to_close():
    cmd = wt.shutdown_command("win32")
    assert cmd == ["shutdown", "/s", "/t", "0"]
    assert "/f" not in cmd


def test_other_systems_power_off():
    assert wt.shutdown_command("linux") == ["systemctl", "poweroff"]


def test_countdown_is_sixty_seconds_as_the_tooltip_says():
    for code in ("en", "de", "tr"):
        i18n.load(code)
        assert str(wt.SHUTDOWN_SECONDS) in i18n.T("tip.shutdown")
    i18n.load("en")


@pytest.mark.parametrize("code", [c for c, _ in i18n.available()])
def test_every_text_exists(code):
    i18n.load(code)
    keys = ["main.shutdown", "main.shutdown_title", "main.shutdown_text", "main.shutdown_now", "main.shutdown_aborted",
            "main.shutdown_postponed", "main.shutdown_not_after_cancel", "main.shutdown_resolve", "main.shutdown_log",
            "main.shutdown_failed", "tip.shutdown"]
    try:
        assert [k for k in keys if not i18n.has(k)] == []
    finally:
        i18n.load("en")
