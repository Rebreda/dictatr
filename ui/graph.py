"""The scene as a graph, and the fractal zoom that moves through it.

Third of the pure modules, with radial_layout and motion: `math` only, so
tests/test_graph.py can hold it to account without a display.

The surfaces used to be three programs that spawned each other. Here they
are nodes: the menu, the chat, the wizard, a submenu, the actions on one
message. Children are named rather than owned, so the structure is a
directed graph and not a tree — the chat is one node whether you reached
it from the menu, the tray, a gesture or a hotkey, and every way in is the
same edge traversal.

Going one level deeper is a zoom. Each level is drawn in its own space,
centred on its hub; entering a bubble composes a translate to where that
bubble sat and a scale small enough that the child's whole ring is exactly
bubble-sized when seen from the parent. So the child was always there and
always that size — you just could not read it yet. One rule, applied
again at every level, which is the whole of the fractal: depth 1 and depth
7 are the same act, and neither the code nor the eye has to learn anything
new to go further in.

Because scale falls off geometrically, only a handful of levels are ever
worth drawing at a given camera position. Depth is unbounded; the work per
frame is not.
"""

import math
from dataclasses import dataclass, field

EPS = 1e-9

# A level is worth drawing while it is between these apparent scales:
# smaller than the first is an unreadable speck, larger than the second is
# a wall you are already through.
MIN_SCALE = 0.02
MAX_SCALE = 40.0
# Where a level too small to read yet fades in, as an apparent scale.
FADE_OUT = 0.08
# ...and where the one you are flying through fades out, as a fraction of
# the descent rather than as a scale.
#
# Scale cannot say this. How big a parent looks at the moment you land on
# its child is bubble/(2*child_extent) inverted -- a property of that one
# ring's proportions, about 4x for these, and not of the traversal. Tuned
# against a scale, the fade either finished before the movement did or,
# as it did here, never finished at all: the level you had just flown
# through sat over the one you had arrived at, four times life size and
# still fully opaque, which reads as two menus at once rather than as
# depth. Depth is what a descent is measured in, so measure it in depth.
PASS_SOLID = 0.25       # the first quarter of a descent leaves it alone
# ...and after that it decays, at a rate that leaves a level a tenth of
# itself by the time you have landed on its child and gone by the time
# you have landed on its grandchild. One faint ghost behind you, so where
# you came from is still legible without competing with where you are.
PASS_RATE = 3.2


class Cycle(ValueError):
    """A loop in the graph. A cycle in a zoom is an infinite fall."""


@dataclass(frozen=True)
class Node:
    """One place you can be.

    *children* are ids, not nodes: that is what makes this a graph rather
    than a tree, and what lets two parents share one child instead of
    each owning a copy of it.
    """

    id: str
    title: str = ""
    icon: str = ""
    children: tuple = ()
    group: str = ""
    kind: str = "ring"          # ring | card
    data: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Step:
    """One descent: where the chosen bubble sat in its parent's space,
    and how much smaller the child's world is than the parent's."""

    dx: float
    dy: float
    scale: float


def descend(slot, bubble, child_extent) -> Step:
    """The step into a bubble at *slot* whose child ring reaches
    *child_extent* from its own hub.

    The scale is the one that makes the child's whole ring exactly as wide
    as the bubble standing in for it, so nothing changes size at the
    moment you commit — the bubble simply turns out to have been the ring
    all along.
    """
    return Step(slot[0], slot[1], bubble / (2 * max(child_extent, EPS)))


class Graph:
    """Nodes by id, with the guarantee that you cannot fall through them."""

    def __init__(self, nodes):
        self.nodes = {n.id: n for n in nodes}
        missing = {c for n in self.nodes.values() for c in n.children
                   if c not in self.nodes}
        if missing:
            raise KeyError(f"children with no node: {sorted(missing)}")
        self._check_acyclic()

    def __contains__(self, node_id):
        return node_id in self.nodes

    def __getitem__(self, node_id):
        return self.nodes[node_id]

    def children(self, node_id):
        return [self.nodes[c] for c in self.nodes[node_id].children]

    def _check_acyclic(self):
        WHITE, GREY, BLACK = 0, 1, 2
        colour = dict.fromkeys(self.nodes, WHITE)

        def walk(node_id, trail):
            colour[node_id] = GREY
            for child in self.nodes[node_id].children:
                if colour[child] == GREY:
                    raise Cycle(" -> ".join([*trail, node_id, child]))
                if colour[child] == WHITE:
                    walk(child, [*trail, node_id])
            colour[node_id] = BLACK

        for node_id in self.nodes:
            if colour[node_id] == WHITE:
                walk(node_id, [])

    def route(self, root, target):
        """The shortest way from *root* to *target*, as node ids.

        Shortest because a node with several parents should be reached the
        short way: the chat is one node, and opening it from the menu
        should not walk you through wherever else it also hangs.
        """
        if root == target:
            return [root]
        seen = {root}
        queue = [[root]]
        while queue:
            trail = queue.pop(0)
            for child in self.nodes[trail[-1]].children:
                if child in seen:
                    continue
                if child == target:
                    return [*trail, child]
                seen.add(child)
                queue.append([*trail, child])
        return None


class Path:
    """Where you are, and how you got there.

    A list of node ids from the root, with the child index taken at each
    step so the camera knows which bubble it zoomed into.
    """

    def __init__(self, graph, root):
        self.graph = graph
        self.ids = [root]
        self.choices = []

    @property
    def depth(self):
        return len(self.ids) - 1

    @property
    def node(self):
        return self.graph[self.ids[-1]]

    def enter(self, index):
        node = self.node
        if not 0 <= index < len(node.children):
            return False
        self.ids.append(node.children[index])
        self.choices.append(index)
        return True

    def back(self):
        if len(self.ids) == 1:
            return False
        self.ids.pop()
        self.choices.pop()
        return True

    def go(self, target):
        """Walk to *target* by the shortest route from the root."""
        route = self.graph.route(self.ids[0], target)
        if route is None:
            return False
        self.ids = [route[0]]
        self.choices = []
        for node_id in route[1:]:
            self.enter(self.graph[self.ids[-1]].children.index(node_id))
        return True


class Camera:
    """Where the eye is on the zoom, given the steps taken to get there.

    *steps* is one Step per descent. Level 0 is the root's own space; the
    camera sits at a fractional *depth*, so 2.4 is most of the way from
    the third level to the fourth and everything in view is mid-flight.
    """

    def __init__(self, steps=()):
        self.steps = list(steps)

    # --- level frames ---------------------------------------------------
    def between(self, base, level):
        """(scale, dx, dy) taking a point in *level*'s space to *base*'s.

        Relative, because absolute does not survive the depth this claims
        to support. A frame's scale is one step's scale per level and a
        step's scale is well under one, so an absolute frame underflows
        somewhere around level fourteen and every ratio taken from it is
        then nonsense or a division by zero. Everything ever drawn is
        within a few levels of the eye, and between two nearby levels the
        numbers are ordinary whatever the absolute depth is.
        """
        if level >= base:
            scale, x, y = 1.0, 0.0, 0.0
            for step in self.steps[base:level]:
                x += scale * step.dx
                y += scale * step.dy
                scale *= step.scale
            return scale, x, y
        scale, x, y = self.between(level, base)     # ...and inverted
        return 1.0 / scale, -x / scale, -y / scale

    def frame(self, level):
        """(scale, dx, dy) taking a point in *level*'s space to the root's.

        Composition of the steps, flattened: a uniform scale and an
        offset, because every step is a translate and a scale and those
        two compose to exactly that.
        """
        return self.between(0, level)

    def to_root(self, level, point):
        scale, x, y = self.frame(level)
        return x + scale * point[0], y + scale * point[1]

    # --- the eye ---------------------------------------------------------
    def _eye(self, depth, top=None):
        """(base, (scale, dx, dy)) — the camera's frame, in base's space.

        *base* is the level the camera is resting on or falling from, so
        the transform returned is between identity and one step, whatever
        the depth. That is the whole reason it is expressed this way.

        Between levels the scale is interpolated in log space, so a zoom
        reads as one steady movement rather than a rush that trails off:
        each doubling takes the same time as the last, which is what
        makes an unbounded zoom feel like a place rather than a fall.
        """
        last = len(self.steps) if top is None else min(len(self.steps), top)
        base = max(0, min(int(math.floor(depth)), last))
        t = depth - base
        if t <= EPS or base >= len(self.steps):
            return base, (1.0, 0.0, 0.0)
        s1, x1, y1 = self.between(base, base + 1)
        return base, (math.exp(math.log(s1) * t), x1 * t, y1 * t)

    def eye(self, depth):
        """(scale, dx, dy) of the frame the camera is resting in, in the
        root's space. The absolute form, for anything asking where the
        camera is rather than what to draw."""
        base, (rs, rx, ry) = self._eye(depth)
        s, x, y = self.frame(base)
        return s * rs, x + s * rx, y + s * ry

    def scale_of(self, level, depth):
        """How big *level* looks from *depth*. 1.0 is at rest."""
        base, (es, _ex, _ey) = self._eye(depth)
        return self.between(base, level)[0] / es

    def place(self, level, depth, viewport):
        """(scale, x, y) to draw *level* at, in viewport pixels.

        The transform a renderer applies before drawing that level in its
        own coordinates: scale about the origin, then translate.
        """
        base, (es, ex, ey) = self._eye(depth)
        scale, x, y = self.between(base, level)
        cx, cy = viewport[0] / 2, viewport[1] / 2
        return scale / es, cx + (x - ex) / es, cy + (y - ey) / es

    def to_viewport(self, level, depth, point, viewport):
        scale, x, y = self.place(level, depth, viewport)
        return x + scale * point[0], y + scale * point[1]

    def from_viewport(self, level, depth, point, viewport):
        """A viewport point back in *level*'s own space — the hit test."""
        scale, x, y = self.place(level, depth, viewport)
        return (point[0] - x) / max(scale, EPS), (point[1] - y) / max(scale, EPS)

    # --- what is worth drawing -------------------------------------------
    def _row(self, level, depth, top):
        """(level, scale, alpha) for one level, or None if it is not
        worth drawing from here."""
        scale = self.scale_of(level, depth)
        if scale < MIN_SCALE or scale > MAX_SCALE:
            return None
        alpha = 1.0
        past = depth - level
        # The deepest level there is never counts as flown through,
        # however far the camera has run past it: a spring overshooting,
        # or the next level being pulled in before it has been committed
        # to, would otherwise fade the only thing on screen to nothing.
        if past > PASS_SOLID and level < top:
            alpha = math.exp(-(past - PASS_SOLID) * PASS_RATE)
        elif scale < FADE_OUT:                   # not yet legible
            alpha = max(0.0, (scale - MIN_SCALE) / (FADE_OUT - MIN_SCALE))
        return (level, scale, alpha) if alpha > 0.004 else None

    def visible(self, depth, levels=None):
        """[(level, scale, alpha)] worth drawing, nearest the eye first.

        Scale falls off geometrically, so however deep the path goes only
        a handful of levels are ever legible at once. This is why depth
        can be unbounded without the frame cost being.
        """
        top = len(self.steps) if levels is None else levels - 1
        base = max(0, min(int(math.floor(depth)), top))
        out = []
        # Outward from the eye in both directions, stopping at the first
        # level not worth drawing. Scale falls off geometrically one way
        # and opacity falls off the other, both monotonically, so the
        # first level that fails is the last that could have passed --
        # and the work per frame stops depending on how deep the path is
        # rather than merely looking as though it does.
        for level in range(base, top + 1):
            row = self._row(level, depth, top)
            if row is None:
                break
            out.append(row)
        for level in range(base - 1, -1, -1):
            row = self._row(level, depth, top)
            if row is None:
                break
            out.append(row)
        out.sort(key=lambda row: abs(row[1] - 1.0))
        return out
