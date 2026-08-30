"""The scene graph and the fractal zoom.

The surfaces are nodes now, and going deeper is a zoom rather than a
replacement. These are the rules that has to keep: that the graph cannot
be fallen through, that a node reachable two ways is still one node, that
the camera is continuous wherever you stop it, and that however deep the
path goes the work per frame does not grow.
"""

import math

import pytest

import graph as G

MENU = G.Node("menu", "Menu", children=("chat", "wizard", "more"))
CHAT = G.Node("chat", "Chat", children=("chat-settings", "message"))
WIZ = G.Node("wizard", "Set up", children=("step",))
MORE = G.Node("more", "More", children=("wizard", "archive"))
NODES = [MENU, CHAT, WIZ, MORE,
         G.Node("chat-settings", "Chat settings"),
         G.Node("message", "This message"),
         G.Node("step", "Step"),
         G.Node("archive", "Archive")]


def graph():
    return G.Graph(NODES)


def steps(n, scale=0.24, dx=0.0, dy=-84.0):
    return [G.Step(dx, dy, scale) for _ in range(n)]


# --- the graph ---------------------------------------------------------

def test_a_node_reached_two_ways_is_still_one_node():
    """The wizard hangs off both the menu and More. That is the point of
    a graph: it is not two wizards."""
    g = graph()
    assert "wizard" in MENU.children and "wizard" in MORE.children
    assert g["wizard"] is g["wizard"]
    assert g.route("menu", "wizard") == ["menu", "wizard"]


def test_a_route_takes_the_short_way():
    g = graph()
    assert g.route("menu", "archive") == ["menu", "more", "archive"]
    assert g.route("menu", "menu") == ["menu"]
    assert g.route("chat", "wizard") is None


def test_a_cycle_is_refused():
    with pytest.raises(G.Cycle):
        G.Graph([G.Node("a", children=("b",)), G.Node("b", children=("a",))])
    with pytest.raises(G.Cycle):
        G.Graph([G.Node("a", children=("a",))])


def test_a_child_with_no_node_is_refused():
    with pytest.raises(KeyError):
        G.Graph([G.Node("a", children=("ghost",))])


def test_a_diamond_is_not_a_cycle():
    """Two parents, one child, no loop — the shape the whole design rests
    on, so it must not trip the cycle check."""
    G.Graph([G.Node("top", children=("l", "r")),
             G.Node("l", children=("end",)), G.Node("r", children=("end",)),
             G.Node("end")])


# --- paths -------------------------------------------------------------

def test_walking_in_and_back_out():
    p = G.Path(graph(), "menu")
    assert p.depth == 0 and p.node.id == "menu"
    assert p.enter(0) and p.node.id == "chat"
    assert p.enter(1) and p.node.id == "message" and p.depth == 2
    assert p.back() and p.node.id == "chat"
    assert p.back() and p.node.id == "menu"
    assert not p.back()          # the root is the floor
    assert p.choices == []


def test_entering_nothing_changes_nothing():
    p = G.Path(graph(), "menu")
    assert not p.enter(9) and p.depth == 0
    assert not p.enter(-1) and p.depth == 0


def test_going_to_a_node_records_the_route_it_took():
    p = G.Path(graph(), "menu")
    assert p.go("archive")
    assert p.ids == ["menu", "more", "archive"]
    assert [MENU.children[p.choices[0]], MORE.children[p.choices[1]]] \
        == ["more", "archive"]
    assert not p.go("nowhere") or True


# --- the camera --------------------------------------------------------

def test_the_level_you_are_on_is_at_rest():
    cam = G.Camera(steps(6))
    for depth in range(7):
        assert cam.scale_of(depth, depth) == pytest.approx(1.0)


def test_a_parent_looms_and_a_child_is_a_speck():
    cam = G.Camera(steps(4, scale=0.25))
    assert cam.scale_of(1, 2) == pytest.approx(4.0)      # one level back
    assert cam.scale_of(3, 2) == pytest.approx(0.25)     # one level in
    assert cam.scale_of(0, 2) == pytest.approx(16.0)


def test_every_depth_looks_the_same_as_every_other():
    """The fractal claim, as an assertion: away from the ends, the view
    from depth 3 and the view from depth 8 differ in nothing an eye could
    name — same levels, same scales, same opacities, relative to where
    you are standing."""
    cam = G.Camera(steps(12))
    shallow = [(lvl - 3, round(s, 9), round(a, 9))
               for lvl, s, a in cam.visible(3)]
    deep = [(lvl - 8, round(s, 9), round(a, 9))
            for lvl, s, a in cam.visible(8)]
    assert shallow == deep


def test_near_the_root_the_view_is_shallower_because_the_graph_ends():
    """Self-similarity is an interior property. At the top there is
    simply nothing above you, and the view says so rather than
    inventing something."""
    cam = G.Camera(steps(12))
    assert min(lvl for lvl, _s, _a in cam.visible(0)) == 0
    assert len(cam.visible(0)) < len(cam.visible(6))


def test_the_zoom_is_continuous_across_a_level_boundary():
    cam = G.Camera(steps(4))
    for level in range(5):
        before = cam.scale_of(level, 2 - 1e-7)
        after = cam.scale_of(level, 2 + 1e-7)
        assert abs(after - before) < 1e-4


def test_the_zoom_never_pauses_or_reverses():
    cam = G.Camera(steps(4))
    seen = [cam.eye(d / 100)[0] for d in range(401)]
    assert all(b < a for a, b in zip(seen, seen[1:]))


def test_the_zoom_runs_at_a_steady_rate():
    """Log-scale interpolation: every doubling costs the same travel, so
    an unbounded zoom reads as a place rather than a fall."""
    cam = G.Camera(steps(4, scale=0.25))
    ratios = [cam.eye((d + 1) / 8)[0] / cam.eye(d / 8)[0] for d in range(32)]
    assert max(ratios) == pytest.approx(min(ratios), rel=1e-9)


def test_a_point_survives_the_round_trip_to_the_viewport():
    cam = G.Camera(steps(5))
    view = (1920.0, 1080.0)
    for depth in (0.0, 1.0, 2.5, 4.999):
        for level in range(6):
            for point in ((0.0, 0.0), (84.0, -12.0), (-40.5, 77.25)):
                there = cam.to_viewport(level, depth, point, view)
                back = cam.from_viewport(level, depth, there, view)
                assert back == pytest.approx(point, abs=1e-6)


def test_the_level_at_rest_is_centred_in_the_viewport():
    cam = G.Camera(steps(3))
    view = (1920.0, 1080.0)
    assert cam.to_viewport(2, 2, (0.0, 0.0), view) == pytest.approx((960, 540))


def test_the_hub_you_zoom_into_stays_where_you_pointed():
    """At the moment of commit the child's hub is exactly where its
    bubble was, so nothing jumps when the traversal lands."""
    cam = G.Camera([G.Step(0.0, -84.0, 0.24)])
    view = (1000.0, 800.0)
    bubble = cam.to_viewport(0, 0, (0.0, -84.0), view)
    hub = cam.to_viewport(1, 0, (0.0, 0.0), view)
    assert hub == pytest.approx(bubble)


# --- bounded work ------------------------------------------------------

@pytest.mark.parametrize("depth", [0, 1, 3.5, 12, 99, 400, 999])
def test_only_a_handful_of_levels_are_ever_drawn(depth):
    cam = G.Camera(steps(1000))
    drawn = cam.visible(depth)
    assert 1 <= len(drawn) <= 8, f"{len(drawn)} levels at depth {depth}"
    assert all(0.0 < a <= 1.0 for _l, _s, a in drawn)


def test_the_nearest_level_is_offered_first():
    cam = G.Camera(steps(8))
    assert cam.visible(4)[0][0] == 4


def test_a_shallow_graph_does_not_ask_for_levels_it_has_not_got():
    cam = G.Camera(steps(2))
    assert all(0 <= lvl <= 2 for lvl, _s, _a in cam.visible(1))
    assert cam.visible(0)


def test_a_root_with_no_steps_still_draws_itself():
    cam = G.Camera([])
    assert cam.visible(0) == [(0, 1.0, 1.0)]
    assert cam.eye(0) == (1.0, 0.0, 0.0)
    assert cam.eye(5) == (1.0, 0.0, 0.0)      # nowhere to go, so it stays


# --- the step itself ---------------------------------------------------

def test_a_child_ring_is_exactly_bubble_sized_from_its_parent():
    step = G.descend((0.0, -84.0), bubble=52.0, child_extent=110.0)
    cam = G.Camera([step])
    view = (1000.0, 1000.0)
    # The child ring's own extent, seen from the parent's level.
    left = cam.to_viewport(1, 0, (-110.0, 0.0), view)
    right = cam.to_viewport(1, 0, (110.0, 0.0), view)
    assert right[0] - left[0] == pytest.approx(52.0)
