"""Gesture classification.

Traces are generated at several screen sizes and the same assertions run
against each: a gesture is a shape, and a shape that only works on the
machine it was tuned on is not one. 1080p, this laptop's 2880x1800, and
a 4K panel stand in for the range.
"""

import math
import pathlib
import re

import pytest

from dictatr import gestures

SCREENS = [(1920, 1080), (2880, 1800), (3840, 2160)]


def trace(points, ms_per_point=12):
    """[(x, y)] -> the (t, x, y) triples a KWin trace carries."""
    return [(i * ms_per_point, x, y) for i, (x, y) in enumerate(points)]


def line(x0, y0, x1, y1, n=12):
    return [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n)
            for i in range(n + 1)]


def shake(w, h, vertical=True, strokes=5, amplitude=0.22):
    """Back and forth across a fraction of the screen height."""
    reach = h * amplitude
    cx, cy = w / 2, h / 2
    pts = []
    for i in range(strokes):
        a, b = (-reach, reach) if i % 2 == 0 else (reach, -reach)
        pts += (line(cx, cy + a, cx, cy + b) if vertical
                else line(cx + a, cy, cx + b, cy))
    return trace(pts, ms_per_point=8)


def circle(w, h, clockwise=True, turns=1.0, radius=0.16, n=40):
    r = h * radius
    cx, cy = w / 2, h / 2
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * turns * i / n
        if not clockwise:
            a = -a
        # Screen y grows downward, so this traces clockwise on screen.
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return trace(pts, ms_per_point=10)


@pytest.mark.parametrize("w,h", SCREENS)
def test_vertical_shake(w, h):
    assert gestures.classify(shake(w, h, vertical=True), h) == "shake-v"


@pytest.mark.parametrize("w,h", SCREENS)
def test_horizontal_shake(w, h):
    assert gestures.classify(shake(w, h, vertical=False), h) == "shake-h"


@pytest.mark.parametrize("w,h", SCREENS)
def test_circles_and_their_direction(w, h):
    assert gestures.classify(circle(w, h, clockwise=True), h) == "circle-cw"
    assert gestures.classify(circle(w, h, clockwise=False), h) == "circle-ccw"


@pytest.mark.parametrize("w,h", SCREENS)
def test_a_drag_is_not_a_gesture(w, h):
    """All the distance of a shake, but it ends somewhere else."""
    pts = trace(line(0.1 * w, 0.1 * h, 0.9 * w, 0.9 * h, n=40))
    assert gestures.classify(pts, h) is None


@pytest.mark.parametrize("w,h", SCREENS)
def test_working_movement_is_not_a_gesture(w, h):
    """Busy pointing: many direction changes, none of them far."""
    pts = []
    x, y = w / 2, h / 2
    for i in range(60):
        x += (0.02 * w) * (1 if i % 2 else -1)
        y += (0.015 * h) * (1 if i % 3 else -1)
        pts.append((x, y))
    assert gestures.classify(trace(pts), h) is None


@pytest.mark.parametrize("w,h", SCREENS)
def test_too_slow_is_not_a_gesture(w, h):
    slow = [(t * 40, x, y) for t, x, y in shake(w, h)]
    assert gestures.classify(slow, h) is None


@pytest.mark.parametrize("w,h", SCREENS)
def test_a_small_wiggle_is_not_a_gesture(w, h):
    assert gestures.classify(shake(w, h, amplitude=0.02), h) is None


def test_parse_reads_what_the_script_sends():
    w, h, pts = gestures.parse("2880x1800 0,100,200 12,110,260 24,120,300")
    assert (w, h) == (2880, 1800)
    assert pts[1] == (12.0, 110.0, 260.0)
    assert gestures.parse("nonsense") == (None, None, [])


def test_a_half_circle_is_not_a_circle():
    w, h = 2880, 1800
    assert gestures.classify(circle(w, h, turns=0.5), h) != "circle-cw"


def test_debug_topics(monkeypatch):
    """Diagnostics are opt-in per topic, so turning one on does not
    subscribe you to every other firehose."""
    from dictatr.settings import DebugSettings

    d = DebugSettings()
    monkeypatch.delenv("DICTATE_DEBUG", raising=False)
    assert "gesture" not in d                      # off by default

    monkeypatch.setenv("DICTATE_DEBUG", "gesture")
    assert "gesture" in d
    assert "backend" not in d

    monkeypatch.setenv("DICTATE_DEBUG", " backend , gesture ")
    assert "gesture" in d and "backend" in d

    monkeypatch.setenv("DICTATE_DEBUG", "all")
    assert "gesture" in d and "anything" in d


# --- the compositor's prefilter and the classifier must agree ----------
#
# The KWin script cannot import any of this, so the numbers it gates on
# are written out twice. What keeps them honest is that each is only
# ever a looser version of the rule gestures.py applies: the script may
# forward something the tray then rejects, but it must never drop
# something the tray would have accepted.

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "ui/kwin/activewindow.js"


def js_consts():
    src = SCRIPT.read_text()
    return {name: float(re.search(rf"var {name} = ([\d.]+)", src).group(1))
            for name in ("GATE", "RETURN_MAX", "SPAN_MS", "STEP", "MAX_POINTS")}


def test_script_gate_does_not_forward_certain_rejects():
    """A trace under MIN_PATH is rejected on arrival, so handing one
    over is a few kilobytes and a wakeup spent to learn nothing."""
    assert js_consts()["GATE"] >= gestures.MIN_PATH


def test_script_prefilter_cannot_drop_a_real_gesture():
    """The script's return check has to be the looser of the two, or a
    gesture the tray would have named never reaches it."""
    assert js_consts()["RETURN_MAX"] >= gestures.MAX_RETURN


def test_script_window_fits_the_classifier():
    assert js_consts()["SPAN_MS"] / 1000.0 <= gestures.MAX_SECONDS


def dense(w, h, kind, rate=125, span_ms=1200):
    """A trace at a real pointer's reporting rate, where the shared
    helpers above are already coarser than the script's STEP and so
    have nothing to thin."""
    n = int(span_ms * rate / 1000)
    pts = []
    for i in range(n):
        u = i / (n - 1)
        if kind == "shake":
            x, y = w / 2, h / 2 + h * 0.22 * math.sin(2 * math.pi * 2 * u)
        else:
            a = 2 * math.pi * u
            x, y = w / 2 + h * 0.16 * math.cos(a), h / 2 + h * 0.16 * math.sin(a)
        pts.append((span_ms * u, x, y))
    return pts


def decimate(points, min_px):
    """What the KWin script now keeps of a trace."""
    kept = [points[0]]
    for t, x, y in points[1:]:
        if math.hypot(x - kept[-1][1], y - kept[-1][2]) >= min_px:
            kept.append((t, x, y))
    return kept


@pytest.mark.parametrize("w,h", SCREENS)
@pytest.mark.parametrize("kind,expected", [("shake", "shake-v"),
                                           ("circle", "circle-cw")])
@pytest.mark.parametrize("rate", [125, 1000])
def test_decimation_does_not_change_the_verdict(w, h, kind, expected, rate):
    """Thinning to one point per STEP screen heights must not move an
    answer, whatever the pointer reports at."""
    full = dense(w, h, kind, rate=rate)
    assert gestures.classify(full, h) == expected
    thinned = decimate(full, js_consts()["STEP"] * h)
    assert gestures.classify(thinned, h) == expected


@pytest.mark.parametrize("w,h", SCREENS)
def test_decimation_makes_a_trace_cost_what_it_is_worth(w, h):
    """The point of thinning is not a fixed saving; it is that the size
    of a trace follows the movement instead of the hardware.

    A gaming mouse reports eight times as often as a touchpad and draws
    the same circle. Undecimated it sends eight times the bytes and
    costs eight times the work at the far end, all of it sub-pixel steps
    that are quantisation noise. Decimated, the gap closes to under two:
    greedy thinning cannot do better, because a coarse input divides
    into STEP with a remainder, but 8x to 2x is the whole point."""
    step = js_consts()["STEP"] * h
    slow = len(decimate(dense(w, h, "circle", rate=125), step))
    fast = len(decimate(dense(w, h, "circle", rate=1000), step))
    raw_ratio = len(dense(w, h, "circle", rate=1000)) / len(dense(w, h, "circle", rate=125))
    assert raw_ratio == pytest.approx(8, rel=0.05)      # what arrives
    assert fast / slow < 2.0                             # what is kept
    assert fast <= len(dense(w, h, "circle", rate=1000)) * 0.25
