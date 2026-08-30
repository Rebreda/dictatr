"""The screenshot editor's arithmetic.

A HiDPI screen is where this goes wrong: the screenshot is 2880 wide
and the pointer reports coordinates in a 1440-wide window, so every
number the user touches has to cross a scale factor before it means
anything to the saved file.
"""

import pytest

from dictatr.markup import Markup, Rect, clamp, fit, rect_from


HIDPI = (2880, 1800)     # a 1440x900 screen at scale 2


def test_fit_centres_and_scales():
    f = fit(*HIDPI, 1440, 900)
    assert f.scale == 0.5 and (f.ox, f.oy) == (0, 0)


def test_a_point_survives_the_round_trip():
    f = fit(*HIDPI, 1440, 900)
    assert f.to_image(*f.to_view(700, 300)) == (700, 300)


def test_pointer_coordinates_become_image_pixels():
    """The whole point: what the pointer says is half of what is saved."""
    f = fit(*HIDPI, 1440, 900)
    assert f.to_image(100, 50) == (200, 100)


def test_a_wider_view_letterboxes_sideways():
    f = fit(1000, 1000, 2000, 1000)
    assert f.scale == 1.0 and f.ox == 500 and f.oy == 0


def test_a_view_with_no_size_does_not_divide_by_zero():
    assert fit(100, 100, 0, 0).scale == 1.0


@pytest.mark.parametrize("corners", [
    (10, 10, 60, 40),        # dragged right and down
    (60, 40, 10, 10),        # dragged left and up
    (60, 10, 10, 40),        # dragged left and down
])
def test_a_rectangle_is_the_same_dragged_any_direction(corners):
    assert rect_from(*corners) == Rect(10, 10, 50, 30)


def test_a_selection_past_the_edge_is_pulled_back_in():
    """Dragging off the screen is how you select a window flush against
    the side of it, so it has to work, not throw."""
    assert clamp(Rect(-20, -10, 100, 60), 2880, 1800) == Rect(0, 0, 80, 50)
    assert clamp(Rect(2840, 1780, 100, 100), 2880, 1800) == \
        Rect(2840, 1780, 40, 20)


def test_no_selection_saves_the_whole_screenshot():
    """Enter straight away means "this picture", not "you forgot"."""
    assert Markup(*HIDPI).crop == Rect(0, 0, 2880, 1800)


def test_a_click_is_not_a_selection():
    m = Markup(*HIDPI)
    m.region = rect_from(100, 100, 101, 100)
    assert m.crop == Rect(0, 0, 2880, 1800)


def test_a_selection_is_what_gets_saved():
    m = Markup(*HIDPI)
    m.region = rect_from(100, 100, 400, 300)
    assert m.crop == Rect(100, 100, 300, 200)


def test_a_stray_click_leaves_no_shape():
    m = Markup(*HIDPI)
    assert m.add("rect", [(10, 10), (10, 10)]) is None
    assert m.shapes == []


def test_a_pen_stroke_is_kept_however_small():
    """Ink is dots as well as lines; a short one is still deliberate."""
    m = Markup(*HIDPI)
    assert m.add("pen", [(10, 10), (10, 11)]) is not None


def test_undo_takes_back_marks_then_the_selection():
    m = Markup(*HIDPI)
    m.region = rect_from(0, 0, 100, 100)
    m.add("rect", [(10, 10), (50, 50)])
    m.undo()
    assert m.shapes == [] and m.region is not None
    m.undo()
    assert m.region is None
