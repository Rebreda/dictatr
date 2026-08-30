"""The spring.

Everything you can grab moves on one of these. What matters is that it
always arrives, that catching it mid-flight continues the movement
instead of restarting it, and that the damping ratio means what it says.
"""

import math

import pytest

import motion as M

SPRINGS = [M.SNAPPY, M.GLIDE, M.FLING, M.ZOOM,
           M.Spring(response=0.2, damping=0.5),
           M.Spring(response=0.6, damping=1.0),
           M.Spring(response=0.4, damping=1.8)]


def flight(spring, frm=0.0, to=1.0, velocity=0.0, steps=400):
    end = spring.duration(frm, to, velocity)
    return [spring.at(frm, to, velocity, end * i / steps)
            for i in range(steps + 1)]


@pytest.mark.parametrize("spring", SPRINGS)
def test_a_spring_starts_exactly_where_it_was(spring):
    value, velocity = spring.at(3.0, 9.0, -2.5, 0.0)
    assert value == 3.0 and velocity == -2.5


@pytest.mark.parametrize("spring", SPRINGS)
def test_a_spring_always_arrives(spring):
    for frm, to, v in ((0, 1, 0), (10, -4, 0), (0, 1, 12.0), (5, 5, 3.0)):
        end = spring.duration(frm, to, v)
        value, velocity = spring.at(frm, to, v, end)
        assert value == pytest.approx(to, abs=spring.epsilon * 2)
        assert spring.settled(value, to, velocity)


@pytest.mark.parametrize("spring", SPRINGS)
def test_a_spring_stays_arrived(spring):
    end = spring.duration(0.0, 1.0)
    for t in (end, end * 2, end * 10):
        value, _v = spring.at(0.0, 1.0, 0.0, t)
        assert value == pytest.approx(1.0, abs=spring.epsilon * 2)


@pytest.mark.parametrize("spring", SPRINGS)
def test_a_spring_is_continuous(spring):
    """No jumps: sampled finely, no step is a leap."""
    values = [v for v, _ in flight(spring, steps=2000)]
    hops = [abs(b - a) for a, b in zip(values, values[1:])]
    assert max(hops) < 0.02


def test_damping_below_one_overshoots_and_comes_back():
    spring = M.Spring(response=0.3, damping=0.5)
    values = [v for v, _ in flight(spring)]
    assert max(values) > 1.0                     # past the mark
    assert values[-1] == pytest.approx(1.0, abs=0.002)


def test_damping_of_one_never_overshoots():
    spring = M.Spring(response=0.3, damping=1.0)
    values = [v for v, _ in flight(spring)]
    assert max(values) <= 1.0 + 1e-9
    assert all(b >= a - 1e-12 for a, b in zip(values, values[1:]))


def test_damping_above_one_crawls_in_without_overshoot():
    spring = M.Spring(response=0.3, damping=2.0)
    values = [v for v, _ in flight(spring)]
    assert max(values) <= 1.0 + 1e-9
    assert all(b >= a - 1e-12 for a, b in zip(values, values[1:]))


def test_a_faster_response_arrives_sooner():
    quick = M.Spring(response=0.2, damping=1.0)
    slow = M.Spring(response=0.8, damping=1.0)
    assert quick.duration(0, 1) < slow.duration(0, 1)


# --- the whole point ---------------------------------------------------

@pytest.mark.parametrize("spring", SPRINGS)
def test_retargeting_mid_flight_keeps_the_velocity(spring):
    """Catch it moving and it carries on moving. A fixed-duration curve
    would restart from zero here, which is the difference between
    physical and mechanical."""
    end = spring.duration(0.0, 1.0)
    value, velocity = spring.at(0.0, 1.0, 0.0, end * 0.3)
    assert abs(velocity) > 0.01, "should still be moving at 30%"
    # A new target, handed the state the old flight was in.
    at_zero, v_at_zero = spring.at(value, 5.0, velocity, 0.0)
    assert at_zero == pytest.approx(value)
    assert v_at_zero == pytest.approx(velocity)
    # ...and it still arrives.
    settle = spring.duration(value, 5.0, velocity)
    landed, speed = spring.at(value, 5.0, velocity, settle)
    assert landed == pytest.approx(5.0, abs=spring.epsilon * 2)
    assert spring.settled(landed, 5.0, speed)


def test_a_retarget_does_not_jump_the_value():
    """Sampled either side of the handover, the value is unchanged: a
    spring interrupted is a spring redirected, not a spring restarted."""
    spring = M.FLING
    end = spring.duration(0.0, 1.0)
    before, velocity = spring.at(0.0, 1.0, 0.0, end * 0.4)
    after, _ = spring.at(before, -2.0, velocity, 1e-6)
    assert after == pytest.approx(before, abs=1e-4)


def test_a_fling_carries_past_its_start():
    """Released with velocity, it goes where it was thrown before it
    comes back — magnetism, for free."""
    spring = M.FLING
    values = [v for v, _ in flight(spring, frm=0.0, to=0.0, velocity=8.0)]
    assert max(values) > 0.2
    assert values[-1] == pytest.approx(0.0, abs=0.002)


# --- the driver's contract ---------------------------------------------

@pytest.mark.parametrize("spring", SPRINGS)
def test_a_spring_already_there_takes_no_time(spring):
    assert spring.duration(1.0, 1.0, 0.0) == 0.0


@pytest.mark.parametrize("spring", SPRINGS)
def test_duration_is_finite_even_when_thrown_hard(spring):
    assert 0 < spring.duration(0.0, 1.0, 5000.0) <= 4.0


def test_settled_is_about_stillness_as_well_as_position():
    spring = M.FLING
    assert not spring.settled(1.0, 1.0, 99.0)   # at the target, still moving
    assert spring.settled(1.0, 1.0, 0.0)
