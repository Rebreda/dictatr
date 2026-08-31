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
from dataclasses import dataclass

EPS = 1e-9
TAU = 2 * math.pi


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


# --- physics ------------------------------------------------------------
# A fixed-duration curve restarts from nothing when it is interrupted,
# which is what makes an interface feel mechanical: catch a bubble
# mid-flight and it forgets it was moving. A spring does not have a
# duration, it has a state, so a new target picks up the velocity the old
# one had. That is the whole difference, and it is why anything you can
# grab moves on one of these and anything you cannot (a fade, a staged
# reveal) stays on a Track.
#
# Solved analytically rather than stepped: exact at any timestep, so a
# dropped frame cannot change where it lands, and testable without a
# display. Written here rather than taken from libadwaita's
# SpringAnimation because that would make libadwaita a runtime dependency
# of a project whose packages currently only Recommend gtk4.

@dataclass(frozen=True)
class Spring:
    """A damped harmonic oscillator, sampled at a time.

    *damping* is the ratio: below 1 overshoots and comes back, 1 is the
    fastest approach without overshoot, above 1 crawls in. *response* is
    roughly how long it takes to get there, in seconds — the useful knob,
    because stiffness alone means nothing without the mass.
    """

    response: float = 0.32
    damping: float = 0.78
    epsilon: float = 0.001

    @property
    def omega(self):
        return TAU / max(self.response, EPS)

    def at(self, frm, to, velocity, t):
        """(value, velocity) at time *t* into the flight."""
        w, z = self.omega, self.damping
        d = frm - to                    # displacement from the target
        if t <= 0.0:
            return frm, velocity
        if abs(z - 1.0) < 1e-6:                       # critical
            decay = math.exp(-w * t)
            c = velocity + w * d
            return to + (d + c * t) * decay, (c - w * (d + c * t)) * decay
        if z < 1.0:                                   # under: overshoots
            wd = w * math.sqrt(1.0 - z * z)
            decay = math.exp(-z * w * t)
            a = d
            b = (velocity + z * w * d) / wd
            sin, cos = math.sin(wd * t), math.cos(wd * t)
            value = to + decay * (a * cos + b * sin)
            speed = decay * ((b * wd - z * w * a) * cos
                             - (a * wd + z * w * b) * sin)
            return value, speed
        # over: two real roots, no overshoot at all
        root = w * math.sqrt(z * z - 1.0)
        r1, r2 = -z * w + root, -z * w - root
        b = (velocity - r1 * d) / (r2 - r1)
        a = d - b
        e1, e2 = math.exp(r1 * t), math.exp(r2 * t)
        return to + a * e1 + b * e2, a * r1 * e1 + b * r2 * e2

    def settled(self, value, to, velocity):
        """Arrived, and stopped. Both, or a spring sitting exactly on its
        target at full speed would count as done."""
        return (abs(value - to) < self.epsilon
                and abs(velocity) < self.epsilon * self.omega)

    def duration(self, frm, to, velocity=0.0, cap=4.0):
        """How long until it has arrived, for a driver that wants an end.

        Solved from the decay envelope rather than searched for. The
        envelope is not just exp(-damping * omega * t): the underdamped
        branch carries an amplitude from both the displacement and the
        velocity, and the critical branch carries a factor of t, so a
        naive bound stops the animation visibly short of its target.
        """
        w, z = self.omega, self.damping
        d = frm - to
        # Solve for well inside the tolerance rather than exactly on it:
        # landing on the boundary means the driver stops at the moment
        # `settled` is still, by a hair, False.
        eps = self.epsilon * 0.25
        if abs(d) < eps and abs(velocity) < eps * w:
            return 0.0

        if abs(z - 1.0) < 1e-6:
            # |d + c t| e^(-wt) <= eps. No closed form, but the fixed
            # point converges in a couple of passes.
            c = abs(velocity + w * d)
            t = math.log(max(abs(d) + c / w, eps) / eps) / w
            for _ in range(4):
                t = math.log(max(abs(d) + c * t, eps) / eps) / w
            return min(cap, t)

        if z < 1.0:
            wd = w * math.sqrt(1.0 - z * z)
            amplitude = math.hypot(d, (velocity + z * w * d) / wd)
            return min(cap, math.log(max(amplitude, eps) / eps) / (z * w))

        # Overdamped: the slower of the two roots is what is still moving.
        root = w * math.sqrt(z * z - 1.0)
        r1, r2 = -z * w + root, -z * w - root
        b = (velocity - r1 * d) / (r2 - r1)
        amplitude = abs(d - b) + abs(b)
        return min(cap, math.log(max(amplitude, eps) / eps) / abs(r1))


# Named springs, so surfaces reach for a feel rather than for numbers.
SNAPPY = Spring(response=0.24, damping=0.82)    # a bubble giving under a press
GLIDE = Spring(response=0.42, damping=1.0)      # a level arriving, no overshoot
FLING = Spring(response=0.55, damping=0.72)     # a released drag, settling
ZOOM = Spring(response=0.50, damping=0.90)      # the camera between depths
BLOOM = Spring(response=0.38, damping=0.66)     # a bubble thrown out of the hub
# Leaving is not arriving played backwards. BLOOM overshoots its orbit
# and settles back, which is what makes an arrival feel thrown; the same
# overshoot on the way in would carry a bubble through the hub and out
# the far side before it vanished. Critical, and quicker: going is not
# the part worth watching.
FOLD = Spring(response=0.22, damping=1.0)


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
