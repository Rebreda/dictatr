"""The radial kit's geometry.

Every assertion here is a bug the old hardcoded layout actually had:
bubbles that touched at ten items, an animation span that hit zero at
ten and went negative at eight, ancestor levels stacked on one orbit,
number keys that ran off the end of the digits, and a solver that could
be asked about a window with no size at all.
"""

import math

import pytest

import radial_layout as L

SPANS = [0.4, 1.0, math.pi / 2, math.pi, 3 * math.pi / 2, L.TAU]
COUNTS = [1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 14, 20, 40, 200]
STYLES = [L.STYLE_MENU, L.STYLE_CARD]


def arcs():
    for span in SPANS:
        yield L.Arc.centred(math.pi / 2, span)
        yield L.Arc.centred(math.pi / 2, -span)   # the other way round


def chord(r, a, b):
    return 2 * r * abs(math.sin((b - a) / 2))


# --- packing -----------------------------------------------------------

@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("n", COUNTS)
def test_neighbours_never_touch(style, n):
    """The whole point of an adaptive radius. At RADIUS=84 the old ring
    put ten 52px bubbles 51.9px apart."""
    for arc in arcs():
        m = L.solve(["g"] * n, arc, style)
        if m.overflow:
            continue          # the tail folds into a submenu instead
        for a, b in zip(m.angles, m.angles[1:]):
            assert chord(m.radius, a, b) >= m.bubble + style.gap * 0.9


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("n", COUNTS)
def test_solve_is_always_usable(style, n):
    for arc in arcs():
        m = L.solve(["g"] * n, arc, style)
        assert m.radius > 0
        assert style.min_bubble <= m.bubble <= style.bubble
        assert len(m.angles) == n
        assert m.size > style.hub


def test_a_full_ring_closes_evenly():
    """A closed arc pays for the gap from the last item back to the
    first, so six items land 60 degrees apart and not 72."""
    m = L.solve(["g"] * 6, L.Arc.full(), L.STYLE_MENU)
    steps = [b - a for a, b in zip(m.angles, m.angles[1:])]
    assert steps == pytest.approx([L.TAU / 6] * 5)


def test_the_menu_keeps_the_geometry_it_has_always_drawn():
    """Six items, full circle, 84px orbit starting at the top — what the
    demo scenes click at and what the menu has always looked like."""
    m = L.solve(["g"] * 6, L.Arc.full(), L.STYLE_MENU)
    assert m.radius == pytest.approx(84)
    assert m.bubble == pytest.approx(52)
    assert m.angles[0] == pytest.approx(-math.pi / 2)


def test_a_crowded_ring_grows_before_it_shrinks():
    fourteen = L.solve(["g"] * 14, L.Arc.full(), L.STYLE_MENU)
    six = L.solve(["g"] * 6, L.Arc.full(), L.STYLE_MENU)
    assert fourteen.radius > six.radius
    assert fourteen.bubble == six.bubble        # grew, did not shrink yet
    assert fourteen.radius <= L.STYLE_MENU.max_radius


def test_a_ring_past_its_reach_shrinks_then_overflows():
    packed = L.solve(["g"] * 24, L.Arc.full(), L.STYLE_MENU)
    assert packed.radius == pytest.approx(L.STYLE_MENU.max_radius)
    assert packed.bubble < L.STYLE_MENU.bubble
    crowd = L.solve(["g"] * 200, L.Arc.full(), L.STYLE_MENU)
    assert crowd.bubble == pytest.approx(L.STYLE_MENU.min_bubble)
    assert crowd.overflow > 0
    # Folding the tail into one "More" bubble has to actually fit.
    kept = 200 - crowd.overflow + 1
    assert L.solve(["g"] * kept, L.Arc.full(), L.STYLE_MENU).overflow == 0


# --- grouping ----------------------------------------------------------

def test_related_items_sit_closer_than_unrelated_ones():
    groups = ["nav", "nav", "card", "card"]
    m = L.solve(groups, L.Arc.centred(math.pi / 2, math.pi), L.STYLE_CARD)
    gaps = [abs(b - a) for a, b in zip(m.angles, m.angles[1:])]
    within = [gaps[0], gaps[2]]
    between = gaps[1]
    assert max(within) < between


def test_grouping_does_not_disturb_a_single_group():
    one = L.solve(["g"] * 6, L.Arc.full(), L.STYLE_MENU)
    same = L.solve([""] * 6, L.Arc.full(), L.STYLE_MENU)
    assert one.angles == pytest.approx(same.angles)


def test_a_closed_ring_pays_for_the_wrap_gap_too():
    pos, total = L.slot_offsets(["a", "a", "b"], closed=True)
    assert pos == [0.0, 1.0, 2.7]
    assert total == pytest.approx(2.7 + 1.7)   # b back round to a


# --- arcs and obstacles ------------------------------------------------

CARD = (-180, -400, 360, 380)     # a chat column sitting above the hub


def test_the_arc_avoids_what_is_above_it():
    arc = L.arc_avoiding((0, 0), 60, 22, [CARD])
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        a = arc.start + arc.span * t
        x, y = 60 * math.cos(a), 60 * math.sin(a)
        assert not (CARD[0] <= x <= CARD[0] + CARD[2]
                    and CARD[1] <= y <= CARD[1] + CARD[3])
    assert arc.span > 0.5          # and it is still a usable sweep


def test_a_bigger_obstacle_leaves_a_narrower_arc():
    small = L.arc_avoiding((0, 0), 60, 20, [(-40, -100, 80, 90)])
    big = L.arc_avoiding((0, 0), 60, 20, [(-200, -100, 400, 90)])
    assert abs(big.span) < abs(small.span)


def test_nothing_in_the_way_is_the_whole_circle():
    arc = L.arc_avoiding((0, 0), 60, 20, [])
    assert arc.closed


def test_nothing_free_falls_back_to_the_whole_circle():
    """Better a ring drawn over the card than no ring at all."""
    arc = L.arc_avoiding((0, 0), 60, 20, [(-500, -500, 1000, 1000)])
    assert arc.closed


def test_an_unallocated_surface_still_gets_a_layout():
    """tools/wizcheck never presents its window, so every widget reports
    a zero-size rect. That must not divide by anything."""
    arc = L.arc_avoiding((0, 0), 0, 0, [(0, 0, 0, 0)], bounds=(0, 0, 0, 0))
    m = L.solve(["g", "g"], arc, L.STYLE_CARD)
    assert m.radius > 0 and len(m.angles) == 2
    assert L.solve([], L.Arc.centred(0, 0), L.STYLE_CARD).angles == ()


def test_a_zero_width_arc_stacks_rather_than_dividing_by_zero():
    m = L.solve(["g"] * 3, L.Arc(1.0, 1.0), L.STYLE_MENU)
    assert m.angles == pytest.approx([1.0, 1.0, 1.0])


# --- depth -------------------------------------------------------------

def test_ancestors_step_outward_and_never_collide():
    live = L.solve(["g"] * 5, L.Arc.full(), L.STYLE_MENU)
    prev = L.Orbit(live.radius, live.bubble, 1.0)
    seen = 0
    for back in range(1, 12):
        orbit = L.ancestor_metrics(live, back, L.STYLE_MENU)
        if orbit is None:
            break
        seen += 1
        assert orbit.radius > prev.radius
        # The two orbits' bubbles cannot overlap, whatever the angles.
        assert orbit.radius - prev.radius >= (orbit.bubble + prev.bubble) / 2
        assert orbit.alpha < prev.alpha
        prev = orbit
    assert seen >= 2, "at least the parent and grandparent should show"


def test_depth_is_bounded_in_drawing_only():
    live = L.solve(["g"] * 5, L.Arc.full(), L.STYLE_MENU)
    assert L.ancestor_metrics(live, 50, L.STYLE_MENU) is None
    assert L.ancestor_metrics(live, 0, L.STYLE_MENU).alpha == 1.0


# --- animation ---------------------------------------------------------

@pytest.mark.parametrize("n", COUNTS)
@pytest.mark.parametrize("total,stagger", [
    (L.ANIM_S, L.STAGGER_S),          # opening
    (L.OUT_S, 0.02),                  # dismissing
    (L.SUB_IN_S, L.SUB_STAGGER_S),    # a submenu bloom
    (L.SUB_OUT_S, L.SUB_STAGGER_S),   # a submenu collapse
])
def test_every_bubble_gets_a_real_slice_of_the_animation(n, total, stagger):
    """`span = total - (n-1)*stagger` was zero at ten items on open and
    negative at eight in a submenu: a divide-by-zero, then a silent
    layout with every bubble but the last stuck invisible at the hub."""
    dur, stag = L.timing(n, total, stagger)
    span = dur - (n - 1) * stag
    assert span >= min(L.MIN_SPAN_S, total) - 1e-9
    assert stag >= 0


def test_timing_leaves_a_small_ring_alone():
    assert L.timing(6) == (L.ANIM_S, L.STAGGER_S)


# --- keyboard ----------------------------------------------------------

def test_number_keys_stop_at_nine():
    """At eleven items the old bound reached KEY_0 + 11, so ':' and ';'
    fired items 10 and 11 while the advertised digits did nothing."""
    assert L.digit_index(L.KEY_1, 11) == 0
    assert L.digit_index(L.KEY_9, 11) == 8
    for keyval in (0x03a, 0x03b, 0x03c):        # : ; <
        assert L.digit_index(keyval, 14) is None


def test_number_keys_stop_at_the_item_count():
    assert L.digit_index(L.KEY_1 + 1, 2) == 1
    assert L.digit_index(L.KEY_1 + 2, 2) is None      # wizcheck asserts this
    assert L.digit_index(L.KEY_1 - 1, 5) is None      # '0' is not an item
    assert L.digit_index(L.KEY_1, 0) is None


# --- arcs --------------------------------------------------------------

def test_an_arc_can_be_read_either_way_round():
    arc = L.Arc.centred(math.pi / 2, math.pi)
    assert arc.reversed().span == pytest.approx(-arc.span)
    assert arc.reversed().mid == pytest.approx(arc.mid)


def test_shrinking_an_arc_never_turns_it_inside_out():
    assert L.Arc(0.0, 0.2).shrunk(1.0).span == pytest.approx(0.0)
    assert L.Arc(0.2, 0.0).shrunk(1.0).span == pytest.approx(0.0)
    assert L.Arc(0.0, 2.0).shrunk(0.5).span == pytest.approx(1.0)
    assert L.Arc(2.0, 0.0).shrunk(0.5).span == pytest.approx(-1.0)
