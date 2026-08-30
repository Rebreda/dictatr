"""How things move: the kit's easing, scheduling and tween geometry.

Companion to radial_layout, and the same bargain. That module says where
a bubble goes; this one says how anything gets from one state to another,
and neither imports GTK, so both are pytest assertions rather than things
you have to watch to believe.

It exists because motion was scattered. The ring had a real animation
engine and everything else set a label, appended a widget or rewrote an
opacity in one frame — an answer arriving as a jump, a pill destroyed
mid-fade, a card that rose while the ring hanging off it did not. A
surface should no more work out its own easing curve than its own orbit
radius.

Three things live here:

    curves      ease_out and friends, on 0..1
    Track       one value moving over one span of a timeline
    Timeline    several of those, staged, as one object with a duration

plus `taper`, the outline of the tether that joins two surfaces while one
hands over to the other.
"""

import math

EPS = 1e-9


def clamp(p, lo=0.0, hi=1.0):
    return lo if p < lo else hi if p > hi else p


# --- curves -------------------------------------------------------------
# All take and return 0..1, all hit both endpoints exactly. Anything that
# animates picks one of these rather than inventing a polynomial.

def linear(p):
    return clamp(p)


def ease_out(p):
    """Fast, then settling. The kit's default: things arrive."""
    return 1 - (1 - clamp(p)) ** 3


def ease_in(p):
    return clamp(p) ** 3


def ease_in_out(p):
    p = clamp(p)
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def overshoot(p, k=1.70158):
    """Past the mark and back. For something that lands with weight."""
    p = clamp(p) - 1
    return p * p * ((k + 1) * p + k) + 1


EASES = {"linear": linear, "ease_out": ease_out, "ease_in": ease_in,
         "ease_in_out": ease_in_out, "overshoot": overshoot}


# --- scheduling ---------------------------------------------------------
class Track:
    """One value moving from *frm* to *to*, once, on a timeline.

    Held at *frm* before it starts and at *to* after it ends, so a track
    can be sampled at any time without the caller knowing its schedule.
    """

    __slots__ = ("delay", "duration", "frm", "to", "ease")

    def __init__(self, frm, to, duration, delay=0.0, ease=ease_out):
        self.frm = frm
        self.to = to
        self.duration = max(duration, 0.0)
        self.delay = max(delay, 0.0)
        self.ease = ease

    @property
    def end(self):
        return self.delay + self.duration

    def at(self, t):
        if t < self.delay:
            return self.frm
        # A track with no duration is over the moment it starts, which
        # has to be decided before the start test or a zero-length track
        # reads as not-yet-begun forever.
        if self.duration <= EPS or t >= self.end:
            return self.to
        e = self.ease((t - self.delay) / self.duration)
        return self.frm + (self.to - self.frm) * e


class Timeline:
    """Named tracks staged against one clock.

    A card arriving is a column fading up, then a ring blooming out of
    its hub a moment later. That used to be a tick callback plus an
    unrelated GLib.timeout_add whose numbers happened to add up; here it
    is one object with one duration, and the stages cannot drift apart.
    """

    def __init__(self, tracks=None, **named):
        self.tracks = dict(tracks or {})
        self.tracks.update(named)

    def add(self, name, *args, **kw):
        self.tracks[name] = Track(*args, **kw)
        return self

    @property
    def duration(self):
        return max((t.end for t in self.tracks.values()), default=0.0)

    def at(self, t):
        return {name: track.at(t) for name, track in self.tracks.items()}

    def final(self):
        return self.at(self.duration)


def stagger(n, total, per=0.05, min_span=0.12):
    """A per-item delay that leaves every item a real slice of *total*.

    Naively, item i starts at i*per and the last one gets
    `total - (n-1)*per` to itself — which is zero at ten items and
    negative at eight in a fast animation, i.e. a divide by zero and then
    a silent layout where everything but the last item never appears.
    Compressing the delay instead means the animation keeps its length
    and every item keeps its span, however many there are.
    """
    if n <= 1 or total <= min_span:
        return total, 0.0
    return total, min(per, (total - min_span) / (n - 1))


# --- the tether ---------------------------------------------------------
def bezier(p0, p1, slack, t):
    """A point on the quadratic bow from *p0* to *p1*.

    *slack* pushes the control point perpendicular to the chord, so a
    line that has not gone taut yet hangs.
    """
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length <= EPS:
        return (x0, y0)
    # Perpendicular to the chord, from the midpoint.
    cx = (x0 + x1) / 2 - dy / length * slack
    cy = (y0 + y1) / 2 + dx / length * slack
    u = 1 - t
    return (u * u * x0 + 2 * u * t * cx + t * t * x1,
            u * u * y0 + 2 * u * t * cy + t * t * y1)


def taper(p0, p1, w0, w1, slack=0.0, extent=1.0, samples=24):
    """The tether's outline: a closed polygon, thick at *p0*, thin at *p1*.

    Returns the points of one closed shape — up one side of the bow and
    back down the other — which is what a cairo path wants. *extent* is
    how far along the curve it has grown, so attaching is extent 0 to 1
    and releasing is the reverse.

    Empty when there is nothing to draw: no length, no extent, or the two
    ends in the same place. A caller can hand it anything.
    """
    extent = clamp(extent)
    if extent <= EPS or math.dist(p0, p1) <= EPS:
        return []
    n = max(int(samples), 2)
    spine = []
    for i in range(n + 1):
        t = extent * i / n
        point = bezier(p0, p1, slack, t)
        spine.append((t, point))

    left, right = [], []
    for i, (t, (x, y)) in enumerate(spine):
        # Tangent from the neighbouring samples, so the ends are not
        # special-cased into a different width than their neighbours.
        ax, ay = spine[max(i - 1, 0)][1]
        bx, by = spine[min(i + 1, n)][1]
        tx, ty = bx - ax, by - ay
        length = math.hypot(tx, ty)
        if length <= EPS:
            continue
        nx, ny = -ty / length, tx / length
        half = (w0 + (w1 - w0) * t) / 2
        left.append((x + nx * half, y + ny * half))
        right.append((x - nx * half, y - ny * half))
    if len(left) < 2:
        return []
    return left + right[::-1]
