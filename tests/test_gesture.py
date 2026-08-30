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
            for name in ("TRAVEL", "LEGS", "DEADZONE", "WINDOW", "NET",
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

        travel = self.cur["dist"] + sum(l["dist"] for l in self.legs)
        span = (self.cur["dist"] * self.cur["dir"]
                + sum(l["dist"] * l["dir"] for l in self.legs))
        if (len(self.legs) + 1 >= self.t["LEGS"]
                and travel >= self.t["TRAVEL"]
                and abs(span) <= self.t["NET"]
                and now_ms - self.last_fired > self.t["COOLDOWN"]):
            self.last_fired = now_ms
            self.legs, self.cur = [], None
            self.fired += 1


def shake(d, clock, low=200, high=560, strokes=4, step=40):
    """Move the pointer between two heights a few times, the way a hand
    does: several samples per stroke, tens of milliseconds apart."""
    for i in range(strokes):
        a, b = (low, high) if i % 2 == 0 else (high, low)
        for y in range(a, b, step if b > a else -step):
            d.motion(y, clock)
            clock += 12
    return clock


def test_a_deliberate_shake_fires():
    d = Detector(thresholds())
    shake(d, 0)
    assert d.fired == 1


def test_ordinary_pointing_does_not_fire():
    t = thresholds()
    d = Detector(t)
    clock = 0
    for i in range(400):            # small jitter, no real travel
        d.motion(400 + (i % 4) * 5, clock)
        clock += 25
    assert d.fired == 0


def test_a_long_drag_does_not_fire():
    """All the travel of a shake, but one direction and it ends far from
    where it began: that is someone dragging, not shaking."""
    t = thresholds()
    d = Detector(t)
    clock = 0
    for y in range(100, 1200, 20):
        d.motion(y, clock)
        clock += 15
    assert d.fired == 0


def test_slow_movement_does_not_fire():
    t = thresholds()
    d = Detector(t)
    clock = 0
    for i in range(8):              # same shape, far too slow
        for y in (200, 560):
            d.motion(y, clock)
            clock += t["WINDOW"]
    assert d.fired == 0


def test_cooldown_stops_a_repeat():
    d = Detector(thresholds())
    clock = shake(d, 0, strokes=12)
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
