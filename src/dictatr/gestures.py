"""What a pointer just drew, from a trace of where it went.

The compositor is the only thing that can see the pointer (see
ui/kwin/activewindow.js), but it is a poor place to decide anything: its
script engine has no timers, no numbers library, and nothing there can
be tested. So it stays dumb -- it buffers points and, when enough
movement has happened to be worth a look, hands the trace over. Every
judgement is made here, where it can be unit-tested against traces
instead of against a person waving a mouse.

Everything is measured in screen heights, not pixels. A gesture that
needs 220px is a different gesture on a 1080p laptop than on a 4K
monitor; a gesture that needs a third of the screen is the same
movement everywhere.
"""

import math

# All thresholds are fractions of the screen height, or radians, or
# seconds. Nothing here is in pixels.
MIN_PATH = 0.45        # total distance drawn, in screen heights
MAX_RETURN = 0.35      # end displacement as a share of distance drawn
MAX_SECONDS = 1.6      # a gesture is a burst, not a journey
MIN_STROKE = 0.07      # a direction change shorter than this is jitter
SHAKE_STROKES = 4      # strokes along one axis
AXIS_BIAS = 0.72       # share of movement on the dominant axis
CIRCLE_TURN = 4.7      # radians (~270 degrees) to call it a circle
CIRCLE_CONSISTENCY = 0.8   # share of turning that goes the same way
# A circle bends a little at every step. A shake is straight lines with
# a half-turn at each end, which sums to the same total turning while
# looking nothing like a circle, so the size of a single step's turn is
# what actually separates them.
CIRCLE_SMOOTH = 1.2    # radians: the sharpest bend a circle may have
CIRCLE_BALANCE = 0.25  # how far from evenly two-dimensional it may be


def parse(blob: str):
    """"W H t0,x0,y0 t1,x1,y1 ..." as the KWin script sends it."""
    head, _, rest = blob.partition(" ")
    try:
        w, h = (int(v) for v in head.split("x"))
    except ValueError:
        return None, None, []
    points = []
    for chunk in rest.split():
        try:
            t, x, y = (float(v) for v in chunk.split(","))
        except ValueError:
            continue
        points.append((t, x, y))
    return w, h, points


def _features(points, height):
    """Shape of the trace, in screen heights and radians."""
    pts = [(t, x / height, y / height) for t, x, y in points]
    path = turn = 0.0
    turn_abs = 0.0
    turn_max = 0.0
    prev_angle = None
    dx_total = dy_total = 0.0
    strokes = {"x": [], "y": []}
    run = {"x": [0.0, 0], "y": [0.0, 0]}   # [distance, direction]

    for (_, x0, y0), (_, x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        step = math.hypot(dx, dy)
        if step == 0:
            continue
        path += step
        dx_total += abs(dx)
        dy_total += abs(dy)

        angle = math.atan2(dy, dx)
        if prev_angle is not None:
            d = (angle - prev_angle + math.pi) % (2 * math.pi) - math.pi
            turn += d
            turn_abs += abs(d)
            turn_max = max(turn_max, abs(d))
        prev_angle = angle

        # Strokes per axis: a reversal ends one and starts the next.
        for axis, delta in (("x", dx), ("y", dy)):
            if delta == 0:
                continue
            direction = 1 if delta > 0 else -1
            dist, running = run[axis]
            if running and direction != running:
                if dist >= MIN_STROKE:
                    strokes[axis].append(dist)
                run[axis] = [abs(delta), direction]
            else:
                run[axis] = [dist + abs(delta), direction]
    for axis in ("x", "y"):
        if run[axis][0] >= MIN_STROKE:
            strokes[axis].append(run[axis][0])

    span = math.hypot(pts[-1][1] - pts[0][1], pts[-1][2] - pts[0][2])
    seconds = (pts[-1][0] - pts[0][0]) / 1000.0
    moved = dx_total + dy_total
    return {
        "path": path,
        "net": span,
        "return_ratio": span / path if path else 1.0,
        "turn_max": turn_max,
        "seconds": seconds,
        "turn": turn,
        "turn_abs": turn_abs,
        "vertical": dy_total / moved if moved else 0.0,
        "strokes_x": len(strokes["x"]),
        "strokes_y": len(strokes["y"]),
    }


def _verdict(f) -> str | None:
    """Name the gesture the features describe, or None.

    Ordinary work is the class that matters: it produces far more traces
    than gestures do, so every rule here is written to reject rather
    than to match."""
    if (f["seconds"] > MAX_SECONDS or f["seconds"] <= 0
            or f["path"] < MIN_PATH
            or f["return_ratio"] > MAX_RETURN):
        return None

    # A circle turns steadily in one direction, bends gently at every
    # step, and is as wide as it is tall.
    if (f["turn_abs"] >= CIRCLE_TURN
            and abs(f["turn"]) >= CIRCLE_CONSISTENCY * f["turn_abs"]
            and f["turn_max"] <= CIRCLE_SMOOTH
            and abs(f["vertical"] - 0.5) <= CIRCLE_BALANCE):
        # Screen y grows downward, so a positive turn is clockwise.
        return "circle-cw" if f["turn"] > 0 else "circle-ccw"

    # A shake goes back and forth along one axis.
    if f["vertical"] >= AXIS_BIAS and f["strokes_y"] >= SHAKE_STROKES:
        return "shake-v"
    if 1 - f["vertical"] >= AXIS_BIAS and f["strokes_x"] >= SHAKE_STROKES:
        return "shake-h"
    return None


def _report(f) -> str:
    """The numbers behind a verdict, for the log while tuning."""
    return (f"path={f['path']:.2f} back={f['return_ratio']:.2f} "
            f"s={f['seconds']:.2f} "
            f"turn={f['turn']:.1f}/{f['turn_abs']:.1f} max={f['turn_max']:.1f} "
            f"vert={f['vertical']:.2f} "
            f"strokes={f['strokes_x']}x/{f['strokes_y']}y")


def judge(points, height: int):
    """The verdict and the numbers behind it, from one pass.

    The tray wants both whenever the gesture debug topic is on, and
    measuring the same trace twice to get them is pure waste on a path
    that runs for every trace the compositor hands over."""
    if height <= 0 or len(points) < 8:
        return None, "too short"
    f = _features(points, height)
    return _verdict(f), _report(f)


def classify(points, height: int) -> str | None:
    """Name the gesture in *points*, or None for ordinary movement."""
    return judge(points, height)[0]


def describe(points, height: int) -> str:
    """The measurements behind what classify() decided."""
    return judge(points, height)[1]
