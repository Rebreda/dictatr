"""The panic-shake detector.

The state machine lives in JavaScript inside the compositor
(ui/kwin/activewindow.js), where nothing can import it. This is the same
machine in Python, kept honest by reading the thresholds out of the
script itself: what is tested is the shape of the rule, and that the
numbers it runs with are the numbers shipped."""

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ui/kwin/activewindow.js"


def thresholds():
    src = SCRIPT.read_text()
    return {name: int(re.search(rf"var {name} = (\d+)", src).group(1))
            for name in ("SWING", "NEEDED", "WINDOW", "COOLDOWN")}


class Detector:
    """Mirror of shake() in the KWin script."""

    def __init__(self, t):
        self.t = t
        self.dir = 0
        self.pivot = -1
        self.sweeps = []
        self.last_fired = -10 ** 9
        self.fired = 0

    def motion(self, y, now_ms):
        if self.pivot < 0:
            self.pivot = y
            return
        travel = y - self.pivot
        heading = 1 if travel > 0 else -1
        if heading != self.dir:
            if abs(travel) >= self.t["SWING"]:
                self.sweeps.append(now_ms)
                while self.sweeps and now_ms - self.sweeps[0] > self.t["WINDOW"]:
                    self.sweeps.pop(0)
                if (len(self.sweeps) >= self.t["NEEDED"]
                        and now_ms - self.last_fired > self.t["COOLDOWN"]):
                    self.last_fired = now_ms
                    self.sweeps = []
                    self.fired += 1
            self.dir = heading
            self.pivot = y
        elif abs(travel) > self.t["SWING"] * 2:
            self.pivot = y - heading * self.t["SWING"]


def test_a_deliberate_shake_fires_once():
    t = thresholds()
    d = Detector(t)
    clock = 0
    for _ in range(4):                      # four full up-down swings
        for y in (200, 700):
            d.motion(y, clock)
            clock += 70
    assert d.fired == 1, "a shake should fire exactly once"


def test_ordinary_pointing_does_not_fire():
    t = thresholds()
    d = Detector(t)
    clock = 0
    for i in range(300):                    # small jittery movements
        d.motion(400 + (i % 5) * 6, clock)
        clock += 30
    assert d.fired == 0


def test_slow_scrolling_does_not_fire():
    t = thresholds()
    d = Detector(t)
    clock = 0
    for _ in range(6):                      # long sweeps, but far too slow
        for y in (150, 800):
            d.motion(y, clock)
            clock += 900
    assert d.fired == 0


def test_cooldown_stops_a_repeat():
    t = thresholds()
    d = Detector(t)
    clock = 0
    for _ in range(10):                     # kept shaking without pause
        for y in (200, 700):
            d.motion(y, clock)
            clock += 70
    assert d.fired == 1


def test_a_zombie_is_not_a_live_session(tmp_path, monkeypatch):
    """A child killed while its launcher lives stays in the process
    table answering kill(pid, 0). Treating that as a live dictation
    pauses the always-on listener forever and pins the tray on
    "recording", so live_pid has to look past it."""
    import os
    from dictatr import runstate

    pidfile = tmp_path / "pid"
    pidfile.write_text(str(os.getpid()))
    assert runstate.live_pid(pidfile) == os.getpid()

    monkeypatch.setattr(runstate, "_zombie", lambda pid: True)
    assert runstate.live_pid(pidfile) is None

    pidfile.write_text("999999999")          # nothing is there at all
    monkeypatch.undo()
    assert runstate.live_pid(pidfile) is None
