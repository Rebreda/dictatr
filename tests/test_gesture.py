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
            for name in ("STROKE", "BIG", "WINDOW", "DEADZONE", "NET",
                         "COOLDOWN")}


class Detector:
    """Mirror of shake() in the KWin script."""

    def __init__(self, t):
        self.t = t
        self.legs = []
        self.cur = None
        self.last_y = -1
        self.last_fired = -10 ** 9
        self.fired = 0

    def motion(self, y, now_ms):
        if self.last_y < 0:
            self.last_y = y
            return
        dy = y - self.last_y
        self.last_y = y
        if dy == 0:
            return
        d = 1 if dy > 0 else -1

        if self.cur is None or d == self.cur["dir"]:
            if self.cur is None:
                self.cur = {"t": now_ms, "dist": 0, "dir": d}
            self.cur["dist"] += abs(dy)
            self.cur["t"] = now_ms
        elif self.cur["dist"] >= self.t["DEADZONE"]:
            self.legs.append(self.cur)
            self.cur = {"t": now_ms, "dist": abs(dy), "dir": d}
        else:
            self.cur = {"t": now_ms, "dist": abs(dy), "dir": d}

        while self.legs and now_ms - self.legs[0]["t"] > self.t["WINDOW"]:
            self.legs.pop(0)

        big = sum(1 for l in self.legs if l["dist"] >= self.t["STROKE"])
        span = sum(l["dist"] * l["dir"] for l in self.legs)
        if self.cur["dist"] >= self.t["STROKE"]:
            big += 1
        span += self.cur["dist"] * self.cur["dir"]

        if (big >= self.t["BIG"] and abs(span) <= self.t["NET"]
                and now_ms - self.last_fired > self.t["COOLDOWN"]):
            self.last_fired = now_ms
            self.legs, self.cur = [], None
            self.fired += 1


def sweep(d, clock, a, b, step=20, dt=10):
    """Move between two heights the way a hand does, sampled."""
    for y in range(a, b, step if b > a else -step):
        d.motion(y, clock)
        clock += dt
    return clock


def test_a_deliberate_shake_fires():
    d = Detector(thresholds())
    clock = 0
    for i in range(4):
        clock = sweep(d, clock, *((200, 560) if i % 2 == 0 else (560, 200)))
    assert d.fired == 1


def test_working_movement_does_not_fire():
    """The log from real use: many direction changes and thousands of
    pixels of travel inside a second, but the strokes are short."""
    t = thresholds()
    d = Detector(t)
    clock = 0
    y = 400
    for i in range(600):
        y += (60 if i % 2 == 0 else -55)      # busy, but no long stroke
        d.motion(y, clock)
        clock += 8
    assert d.fired == 0


def test_a_long_drag_does_not_fire():
    d = Detector(thresholds())
    clock = sweep(d, 0, 100, 1200)
    assert d.fired == 0


def test_two_long_strokes_are_not_enough():
    """Down and back up is how a pointer reaches a menu and returns."""
    d = Detector(thresholds())
    clock = sweep(d, 0, 200, 560)
    sweep(d, clock, 560, 200)
    assert d.fired == 0


def test_slow_movement_does_not_fire():
    t = thresholds()
    d = Detector(t)
    clock = 0
    for i in range(6):                        # the right shape, too slow
        a, b = (200, 560) if i % 2 == 0 else (560, 200)
        clock = sweep(d, clock, a, b, dt=t["WINDOW"] // 4)
    assert d.fired == 0


def test_cooldown_stops_a_repeat():
    d = Detector(thresholds())
    clock = 0
    for i in range(12):
        clock = sweep(d, clock, *((200, 560) if i % 2 == 0 else (560, 200)))
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
