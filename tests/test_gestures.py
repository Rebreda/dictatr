"""Gesture classification.

Traces are generated at several screen sizes and the same assertions run
against each: a gesture is a shape, and a shape that only works on the
machine it was tuned on is not one. 1080p, this laptop's 2880x1800, and
a 4K panel stand in for the range.
"""

import math

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
