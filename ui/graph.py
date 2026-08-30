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
# Where it fades, as a fraction of the way to those limits.
FADE_IN = 4.0
FADE_OUT = 0.08


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
    def frame(self, level):
        """(scale, dx, dy) taking a point in *level*'s space to the root's.

        Composition of the steps, flattened: a uniform scale and an
        offset, because every step is a translate and a scale and those
        two compose to exactly that.
        """
        scale, x, y = 1.0, 0.0, 0.0
        for step in self.steps[:level]:
            x += scale * step.dx
            y += scale * step.dy
            scale *= step.scale
        return scale, x, y

    def to_root(self, level, point):
        scale, x, y = self.frame(level)
        return x + scale * point[0], y + scale * point[1]

    # --- the eye ---------------------------------------------------------
    def eye(self, depth):
        """(scale, dx, dy) of the frame the camera is resting in.

        Between levels the scale is interpolated in log space, so a zoom
        reads as one steady movement rather than a rush that trails off:
        each doubling takes the same time as the last, which is what
        makes an unbounded zoom feel like a place rather than a fall.
        """
        low = max(0, min(int(math.floor(depth)), len(self.steps)))
        t = depth - low
        s0, x0, y0 = self.frame(low)
        if t <= EPS or low >= len(self.steps):
            return s0, x0, y0
        s1, x1, y1 = self.frame(low + 1)
        return (math.exp(math.log(s0) + (math.log(s1) - math.log(s0)) * t),
                x0 + (x1 - x0) * t,
                y0 + (y1 - y0) * t)

    def scale_of(self, level, depth):
        """How big *level* looks from *depth*. 1.0 is at rest."""
        eye_scale = self.eye(depth)[0]
        return self.frame(level)[0] / max(eye_scale, EPS)

    def place(self, level, depth, viewport):
        """(scale, x, y) to draw *level* at, in viewport pixels.

        The transform a renderer applies before drawing that level in its
        own coordinates: scale about the origin, then translate.
        """
        eye_scale, ex, ey = self.eye(depth)
        scale, x, y = self.frame(level)
        cx, cy = viewport[0] / 2, viewport[1] / 2
        return (scale / eye_scale,
                cx + (x - ex) / eye_scale,
                cy + (y - ey) / eye_scale)

    def to_viewport(self, level, depth, point, viewport):
        scale, x, y = self.place(level, depth, viewport)
        return x + scale * point[0], y + scale * point[1]

    def from_viewport(self, level, depth, point, viewport):
        """A viewport point back in *level*'s own space — the hit test."""
        scale, x, y = self.place(level, depth, viewport)
        return (point[0] - x) / max(scale, EPS), (point[1] - y) / max(scale, EPS)

    # --- what is worth drawing -------------------------------------------
    def visible(self, depth, levels=None):
        """[(level, scale, alpha)] worth drawing, nearest the eye first.

        Scale falls off geometrically, so however deep the path goes only
        a handful of levels are ever legible at once. This is why depth
        can be unbounded without the frame cost being.
        """
        top = len(self.steps) if levels is None else levels - 1
        out = []
        for level in range(0, top + 1):
            scale = self.scale_of(level, depth)
            if scale < MIN_SCALE or scale > MAX_SCALE:
                continue
            alpha = 1.0
            if scale > FADE_IN:                      # zooming past it
                alpha = max(0.0, 1.0 - (scale - FADE_IN) / (MAX_SCALE - FADE_IN))
            elif scale < FADE_OUT:                   # not yet legible
                alpha = max(0.0, (scale - MIN_SCALE) / (FADE_OUT - MIN_SCALE))
            if alpha > 0.004:
                out.append((level, scale, alpha))
        out.sort(key=lambda row: abs(row[1] - 1.0))
        return out
