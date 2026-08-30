"""Run-state files: what one dictatr process tells the others."""

import os

from dictatr import runstate


def test_a_zombie_is_not_a_live_session(tmp_path, monkeypatch):
    """A child killed while its launcher lives stays in the process
    table answering kill(pid, 0). Treating that as a live dictation
    pauses the always-on listener forever and pins the tray on
    "recording", so live_pid has to look past it."""
    pidfile = tmp_path / "pid"
    pidfile.write_text(str(os.getpid()))
    assert runstate.live_pid(pidfile) == os.getpid()

    monkeypatch.setattr(runstate, "_zombie", lambda pid: True)
    assert runstate.live_pid(pidfile) is None
    monkeypatch.undo()

    pidfile.write_text("999999999")          # nothing is there at all
    assert runstate.live_pid(pidfile) is None
    assert runstate.live_pid(tmp_path / "absent") is None


def test_mode_and_app_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(runstate, "RUN", tmp_path)
    monkeypatch.setattr(runstate, "MODE", tmp_path / "mode")
    monkeypatch.setattr(runstate, "APP", tmp_path / "app")
    assert runstate.read_mode() is None and runstate.read_app() is None
    runstate.write_mode("ask")
    runstate.write_app("codium")
    assert runstate.read_mode() == "ask"
    assert runstate.read_app() == "codium"
