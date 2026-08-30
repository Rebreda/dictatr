"""Where the bubbles go: the radial kit's geometry, with no GTK in it.

Every surface in the family — the menu's ring, the chat card's chrome, the
wizard's — is a hub with satellites on an orbit. Only the arithmetic of
that lives here: given how many items there are, which of them belong
together, and how much of the circle is free, this says what radius to
use, how big the bubbles are and what angle each one sits at.

It is separate from radial.py because it is separately testable. The
project venv has no PyGObject, so anything that imports gi can only be
exercised by hand on a live display; this module imports `math` and
nothing else, so every rule below is a pytest assertion in
tests/test_radial_layout.py rather than something you have to look at to
believe.

Angles are radians in GTK's coordinate sense: +y points down, so angle 0
is to the right, pi/2 is straight *down*, and an increasing angle sweeps
clockwise on screen. A full circle starting at -pi/2 therefore begins at
the top and goes clockwise, which is what the menu has always drawn.
"""

import math
from dataclasses import dataclass

import motion

TAU = 2 * math.pi
EPS = 1e-9

# Items in different groups sit this many extra slot-widths apart. Enough
# that "these two belong together" reads at a glance, not so much that a
# ring of pairs looks like separate rings.
GROUP_GAP = 0.7

# --- animation ---------------------------------------------------------
ANIM_S = 0.45        # opening twirl duration
STAGGER_S = 0.05     # per-bubble delay while opening
TWIRL_RAD = 2.2      # extra rotation that unwinds during the twirl
OUT_S = 0.30         # dismissal twirl-out duration
SUB_OUT_S = 0.18     # ring collapse half of a submenu hop
SUB_IN_S = 0.26      # ring bloom half of a submenu hop
SUB_STAGGER_S = 0.04
MIN_SPAN_S = 0.12    # no bubble gets less animation than this

# An ancestor level is pushed outward, shrunk and dimmed, so the level
# you came from stays a click away instead of a memory.
ANCESTOR_SHRINK = 0.85   # per level back
ANCESTOR_FADE = 0.65     # per level back, after the first
PARENT_ALPHA = 0.34      # the immediate parent's opacity

# Keysyms, so this module can decide keyboard mapping without importing
# Gdk. Gdk.KEY_1 is 0x031 and Gdk.KEY_9 is 0x039.
KEY_1 = 0x031
KEY_9 = 0x039


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


@dataclass(frozen=True)
class Arc:
    """A sweep of the circle, from *start* to *end* in radians.

    Signed: end < start sweeps the other way round, which is how a
    surface asks for its items left-to-right instead of right-to-left.
    """

    start: float
    end: float

    @property
    def span(self) -> float:
        return self.end - self.start

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2

    @property
    def closed(self) -> bool:
        """A full circle, where the last item's gap to the first counts."""
        return abs(self.span) >= TAU - 1e-6

    @staticmethod
    def full(start: float = -math.pi / 2) -> "Arc":
        return Arc(start, start + TAU)

    @staticmethod
    def centred(mid: float, span: float) -> "Arc":
        return Arc(mid - span / 2, mid + span / 2)

    def reversed(self) -> "Arc":
        return Arc(self.end, self.start)

    def shrunk(self, pad: float) -> "Arc":
        """Pull both ends in by *pad*, never past the middle."""
        if abs(self.span) <= 2 * pad:
            return Arc(self.mid, self.mid)
        step = pad if self.span > 0 else -pad
        return Arc(self.start + step, self.end - step)

    def contains(self, angle: float) -> bool:
        lo, hi = sorted((self.start, self.end))
        if self.closed:
            return True
        # Compare on the same turn as the arc.
        a = lo + (angle - lo) % TAU
        return a <= hi + EPS


@dataclass(frozen=True)
class Style:
    """What a surface looks like — never where its items go.

    A ring that *is* the surface (the menu) can afford to be big. A ring
    that is the chrome under a card should not compete with the card, so
    it gets its own style rather than its own layout code.
    """

    radius: float = 84        # the orbit when the items fit comfortably
    bubble: float = 52        # preferred satellite diameter
    min_bubble: float = 30    # shrink no further; fold into a submenu instead
    hub: float = 58           # the middle button
    gap: float = 10           # clear space between neighbouring bubbles
    max_radius: float = 170   # how far the live orbit may reach
    max_reach: float = 240    # how far an ancestor orbit may reach
    icon_ratio: float = 0.42  # icon pixels, as a fraction of the bubble

    @property
    def min_radius(self) -> float:
        """Close enough to touch the hub, and no closer."""
        return self.hub / 2 + self.bubble / 2 + self.gap


# The menu at six items must come out at radius 84 with 52px bubbles —
# that is what it has always drawn, and what the demo scenes click at.
STYLE_MENU = Style()
# The chat card and the wizard: quieter, because the conversation above
# them is the point and the chrome is not.
# max_reach is what makes the widget 340px square, which is what fits
# inside a 360px card: two levels of ancestry are drawn, and anything
# deeper is counted rather than shown.
STYLE_CARD = Style(radius=56, bubble=34, min_bubble=24, hub=44, gap=8,
                   max_radius=96, max_reach=170)


@dataclass(frozen=True)
class Metrics:
    """One level's solved geometry."""

    radius: float
    bubble: float
    angles: tuple[float, ...]
    overflow: int = 0   # trailing items that did not fit at any size

    @property
    def size(self) -> float:
        """The square side this level needs, centred on the hub."""
        return 2 * (self.radius + self.bubble / 2)


@dataclass(frozen=True)
class Orbit:
    """Where an ancestor level is parked while a deeper one is live."""

    radius: float
    bubble: float
    alpha: float


def slot_offsets(groups, closed: bool) -> tuple[list[float], float]:
    """Item positions in slot units, plus the total the arc divides into.

    One slot per item, plus GROUP_GAP extra wherever the group changes.
    This is the whole of "related items sit closer together": neighbours
    inside a group are one slot apart, neighbours across a boundary are
    1.7, and the radius solver below sizes the ring off the *smallest*
    of those gaps so the tight pairs are the ones that just fit.
    """
    groups = list(groups)
    n = len(groups)
    if n == 0:
        return [], 0.0
    pos = [0.0]
    for i in range(1, n):
        step = 1.0 + (GROUP_GAP if groups[i] != groups[i - 1] else 0.0)
        pos.append(pos[-1] + step)
    if not closed:
        return pos, pos[-1]
    # A closed arc also has to pay for the gap from the last item back
    # round to the first.
    wrap = 1.0 + (GROUP_GAP if n > 1 and groups[-1] != groups[0] else 0.0)
    return pos, pos[-1] + wrap


def _capacity(arc: Arc, style: Style) -> int:
    """How many items fit on this arc at the smallest allowed bubble."""
    chord = style.min_bubble + style.gap
    ratio = chord / (2 * style.max_radius)
    delta = math.pi if ratio >= 1.0 else 2 * math.asin(ratio)
    slots = abs(arc.span) / delta
    fits = int(math.floor(slots + EPS))
    return max(1, fits if arc.closed else fits + 1)


def solve(groups, arc: Arc, style: Style = STYLE_MENU, *,
          hub: float | None = None) -> Metrics:
    """Radius, bubble size and per-item angles for one level.

    Never raises and never returns something unusable: zero items, one
    item, a zero-width arc and two hundred items all come back with
    something a caller can draw. That matters because tools/wizcheck
    builds surfaces that are never allocated, so this gets asked about
    windows with no size at all.
    """
    groups = list(groups)
    n = len(groups)
    hub_d = style.hub if hub is None else hub
    r_min = hub_d / 2 + style.bubble / 2 + style.gap
    # The orbit a surface has always used, when the items fit on it.
    # Packing only ever pushes outward from here, so a ring with room to
    # spare keeps the proportions it was designed with instead of
    # collapsing onto the hub.
    nominal = max(style.radius, r_min)

    if n == 0:
        return Metrics(nominal, style.bubble, ())
    if n == 1:
        return Metrics(nominal, style.bubble, (arc.mid,))

    pos, total = slot_offsets(groups, arc.closed)
    span = abs(arc.span)
    if total <= EPS or span <= EPS:
        # Degenerate: everything would land on one angle. Stack them at
        # the arc's middle rather than dividing by zero.
        return Metrics(nominal, style.bubble, tuple(arc.mid for _ in range(n)))

    delta = span / total          # the smallest adjacent step, one slot
    bubble = style.bubble
    if delta >= math.pi:
        radius = nominal
    else:
        # Chord, not arc length: at small n the arc overestimates how far
        # apart two bubbles actually are, and the ring comes out loose.
        radius = max(nominal,
                     (bubble + style.gap) / (2 * math.sin(delta / 2)))

    overflow = 0
    if radius > style.max_radius:
        radius = style.max_radius
        # Cannot go further out, so come down in size instead.
        bubble = 2 * radius * math.sin(delta / 2) - style.gap
        if bubble < style.min_bubble:
            bubble = style.min_bubble
            cap = _capacity(arc, style)
            if cap < n:
                # The caller folds the tail into a submenu of its own.
                # A second orbit would double the input region and put
                # two bubbles under one direction from the hub.
                overflow = n - cap + 1
        radius = max(radius, hub_d / 2 + bubble / 2 + style.gap)

    angles = tuple(arc.start + arc.span * p / total for p in pos)
    return Metrics(radius, bubble, angles, overflow)


def ancestor_metrics(live: Metrics, back: int,
                     style: Style = STYLE_MENU) -> Orbit | None:
    """Where the level *back* steps behind the live one is parked.

    Additive, one bubble-width at a time. The old kit multiplied the
    radius by a constant per level, which put every ancestor deeper than
    the first on top of each other; stepping outward by the two bubbles'
    own sizes cannot collide by construction.

    Returns None once the orbit would pass the style's reach — that
    level is hidden outright rather than drawn at some invisible alpha,
    because a transparent widget still swallows clicks. Depth stays
    unbounded; only the drawing of it is capped.
    """
    if back <= 0:
        return Orbit(live.radius, live.bubble, 1.0)
    radius, bubble = live.radius, live.bubble
    for k in range(1, back + 1):
        nxt = max(style.min_bubble, live.bubble * ANCESTOR_SHRINK ** k)
        radius += bubble / 2 + nxt / 2 + style.gap * 1.5
        bubble = nxt
    if radius + bubble / 2 > style.max_reach:
        return None
    return Orbit(radius, bubble, PARENT_ALPHA * ANCESTOR_FADE ** (back - 1))


def _disc_clear(cx, cy, r, obstacles, bounds) -> bool:
    """Is the disc at (cx, cy) clear of every rect, and inside bounds?"""
    if bounds is not None:
        bx, by, bw, bh = bounds
        if bw > 0 and bh > 0 and not (
                bx <= cx - r and cx + r <= bx + bw
                and by <= cy - r and cy + r <= by + bh):
            return False
    for ox, oy, ow, oh in obstacles or ():
        if ow <= 0 or oh <= 0:
            continue
        # Closest point on the rect to the disc's centre.
        nx = _clamp(cx, ox, ox + ow)
        ny = _clamp(cy, oy, oy + oh)
        if (cx - nx) ** 2 + (cy - ny) ** 2 < r * r:
            return False
    return True


def arc_avoiding(center, radius: float, clearance: float, obstacles, *,
                 prefer: float = math.pi / 2, bounds=None,
                 samples: int = 180) -> Arc:
    """The longest sweep of the circle that hits nothing.

    *obstacles* and *bounds* are (x, y, w, h) rects in the same space as
    *center*. This is how a ring ends up under the card it belongs to
    without anyone writing down an angle: the card says which widgets
    are in the way, and the free arc is whatever is left.

    Ties go to the run containing *prefer* (straight down, by default).
    With nothing free — or nothing measured yet, which is every surface
    that has not been allocated — the whole circle comes back, because a
    ring drawn over the card beats no ring at all.
    """
    cx, cy = center
    step = TAU / samples
    free = [
        _disc_clear(cx + radius * math.cos(i * step),
                    cy + radius * math.sin(i * step),
                    clearance, obstacles, bounds)
        for i in range(samples)
    ]
    if all(free) or not any(free):
        return Arc.centred(prefer, TAU)

    best_start, best_len = 0, 0
    best_has_prefer = False
    i = 0
    # Walk twice round so a run spanning the seam is found whole.
    while i < samples:
        if not free[i]:
            i += 1
            continue
        j = i
        while j - i < samples and free[j % samples]:
            j += 1
        run_len = j - i
        run = Arc(i * step, j * step)
        has_prefer = run.contains(prefer)
        better = (run_len > best_len
                  or (run_len == best_len and has_prefer and not best_has_prefer))
        if better:
            best_start, best_len, best_has_prefer = i, run_len, has_prefer
        i = j
    arc = Arc(best_start * step, (best_start + best_len) * step)
    return arc.shrunk(step / 2)


def slot_offset(n: int, i: int, style: Style = STYLE_MENU,
                arc: Arc | None = None) -> tuple[float, float]:
    """Where bubble *i* of *n* sits, relative to the hub's centre.

    For anything that has to aim at a bubble from outside the widget —
    the demo stage drives a real pointer at the menu, and used to do it
    with the orbit radius written out by hand next to a comment
    explaining the arithmetic.
    """
    metrics = solve(["g"] * n, arc or Arc.full(), style)
    angle = metrics.angles[i]
    return (metrics.radius * math.cos(angle),
            metrics.radius * math.sin(angle))


def timing(n: int, total: float = ANIM_S, stagger: float = STAGGER_S,
           min_span: float = MIN_SPAN_S) -> tuple[float, float]:
    """Duration and per-bubble delay that stay sane at any item count.

    The old kit computed the per-bubble span as `total - (n-1)*stagger`
    and divided by it, which is exactly zero at ten items on the root
    ring and negative at eight in a submenu — a crash and, worse, a
    silent layout where every bubble but the last stayed invisible. The
    fix is to compress the stagger rather than the span.
    """
    return motion.stagger(n, total, stagger, min_span)


def digit_index(keyval: int, n: int) -> int | None:
    """Which item a number key selects, or None.

    Only 1..9, and only up to *n*. The old bound was
    `KEY_1 <= keyval <= KEY_0 + n`, which past nine items walks straight
    into the next keysyms: at eleven items ':' and ';' fired items 10
    and 11, and the digits their tooltips advertised did nothing.
    """
    if n <= 0:
        return None
    if KEY_1 <= keyval <= min(KEY_9, KEY_1 + n - 1):
        return keyval - KEY_1
    return None
