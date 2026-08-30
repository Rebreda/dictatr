"""The kit's easing, scheduling and tween geometry.

Motion used to be one real engine (the ring's) and a dozen places that
set a value in a single frame. These are the rules the shared one has to
keep so the surfaces can all be built on it.
"""

import math

import pytest

import motion as M

CURVES = [M.linear, M.ease_out, M.ease_in, M.ease_in_out, M.overshoot]


# --- curves ------------------------------------------------------------

@pytest.mark.parametrize("curve", CURVES)
def test_a_curve_hits_both_ends_exactly(curve):
    assert curve(0.0) == pytest.approx(0.0)
    assert curve(1.0) == pytest.approx(1.0)


@pytest.mark.parametrize("curve", CURVES)
def test_a_curve_is_clamped_outside_its_span(curve):
    assert curve(-5.0) == pytest.approx(0.0)
    assert curve(5.0) == pytest.approx(1.0)


@pytest.mark.parametrize("curve", [M.linear, M.ease_out, M.ease_in,
                                   M.ease_in_out])
def test_a_curve_never_goes_backwards(curve):
    values = [curve(i / 200) for i in range(201)]
    assert all(b >= a - 1e-12 for a, b in zip(values, values[1:]))


def test_overshoot_goes_past_the_mark_and_comes_back():
    """The one curve that is allowed to leave 0..1, on purpose."""
    peak = max(M.overshoot(i / 200) for i in range(201))
    assert peak > 1.0
    assert M.overshoot(1.0) == pytest.approx(1.0)


# --- tracks ------------------------------------------------------------

def test_a_track_is_held_before_and_after_its_span():
    t = M.Track(10.0, 20.0, duration=0.4, delay=0.2)
    assert t.at(0.0) == 10.0
    assert t.at(0.2) == 10.0
    assert t.at(0.6) == 20.0
    assert t.at(99.0) == 20.0
    assert t.end == pytest.approx(0.6)


def test_a_track_moves_monotonically_between_its_ends():
    t = M.Track(0.0, 100.0, duration=0.5)
    values = [t.at(i / 100) for i in range(101)]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:]))
    assert values[0] == 0.0 and values[-1] == pytest.approx(100.0)


def test_a_zero_length_track_is_already_finished():
    t = M.Track(0.0, 1.0, duration=0.0)
    assert t.at(0.0) == 1.0


# --- timelines ---------------------------------------------------------

def stage():
    """The card's arrival: the column settles, then the ring blooms."""
    return M.Timeline(
        column=M.Track(0.0, 1.0, duration=0.22),
        lift=M.Track(26.0, 0.0, duration=0.22),
        ring=M.Track(0.0, 1.0, duration=0.45, delay=0.12),
    )


def test_a_timeline_is_as_long_as_its_last_track():
    assert stage().duration == pytest.approx(0.57)


def test_a_timeline_settles_exactly_on_its_final_values():
    tl = stage()
    assert tl.at(tl.duration) == tl.final()
    assert tl.final() == {"column": 1.0, "lift": 0.0, "ring": 1.0}


def test_a_timeline_is_continuous_across_a_stage_boundary():
    """A track that has not started must not jump when it does."""
    tl = stage()
    before = tl.at(0.12 - 1e-6)["ring"]
    after = tl.at(0.12 + 1e-6)["ring"]
    assert abs(after - before) < 1e-3


def test_a_stage_that_has_not_started_holds_its_first_value():
    assert stage().at(0.0)["ring"] == 0.0


# --- stagger -----------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 6, 8, 10, 20, 200])
@pytest.mark.parametrize("total,per", [(0.45, 0.05), (0.26, 0.04),
                                       (0.30, 0.02), (0.18, 0.04)])
def test_every_item_keeps_a_real_slice_of_the_animation(n, total, per):
    """`total - (n-1)*per` was zero at ten items and negative at eight in
    a submenu: a divide by zero, then a silent layout with every item but
    the last stuck invisible."""
    duration, delay = M.stagger(n, total, per)
    assert duration == total
    assert delay >= 0.0
    assert duration - (n - 1) * delay >= min(0.12, total) - 1e-9


def test_stagger_leaves_a_short_list_alone():
    assert M.stagger(6, 0.45, 0.05) == (0.45, 0.05)


# --- the tether --------------------------------------------------------
ORIGIN, TARGET = (100.0, 100.0), (300.0, 220.0)


def widths(points):
    """Outline back to (spine point, width) pairs: the polygon is one
    side forward and the other side backward, so pair i with -1-i."""
    half = len(points) // 2
    return [math.dist(points[i], points[-1 - i]) for i in range(half)]


def test_a_tether_tapers_from_thick_to_thin():
    got = widths(M.taper(ORIGIN, TARGET, 18.0, 2.0))
    assert got[0] == pytest.approx(18.0, abs=0.5)
    assert got[-1] == pytest.approx(2.0, abs=0.5)
    assert all(b <= a + 1e-6 for a, b in zip(got, got[1:]))


def test_a_tether_is_a_closed_outline_with_two_sides():
    points = M.taper(ORIGIN, TARGET, 18.0, 2.0, samples=24)
    assert len(points) % 2 == 0
    assert len(points) >= 40


def test_a_tether_starts_at_the_origin_and_reaches_the_target():
    points = M.taper(ORIGIN, TARGET, 12.0, 2.0)
    assert min(math.dist(p, ORIGIN) for p in points) < 12.0
    assert min(math.dist(p, TARGET) for p in points) < 2.0


def test_extent_grows_the_tether_along_the_curve():
    reach = [max(math.dist(p, ORIGIN)
                 for p in M.taper(ORIGIN, TARGET, 12.0, 2.0, extent=e))
             for e in (0.25, 0.5, 0.75, 1.0)]
    assert all(b > a for a, b in zip(reach, reach[1:]))
    assert reach[-1] == pytest.approx(math.dist(ORIGIN, TARGET), abs=12.0)


def test_slack_bows_the_tether_off_the_straight_line():
    def furthest(slack):
        points = M.taper(ORIGIN, TARGET, 2.0, 2.0, slack=slack)
        (x0, y0), (x1, y1) = ORIGIN, TARGET
        length = math.dist(ORIGIN, TARGET)
        return max(abs((x1 - x0) * (y0 - py) - (x0 - px) * (y1 - y0)) / length
                   for px, py in points)
    assert furthest(0.0) < 2.0          # taut: only the stroke's own width
    assert furthest(40.0) > 15.0        # slack: visibly bowed


def test_a_tether_with_nothing_to_draw_is_empty():
    assert M.taper(ORIGIN, TARGET, 12.0, 2.0, extent=0.0) == []
    assert M.taper(ORIGIN, ORIGIN, 12.0, 2.0) == []
    assert M.taper(ORIGIN, TARGET, 12.0, 2.0, extent=-1.0) == []
