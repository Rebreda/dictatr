"""Handing one surface over to the next, with the thread still attached.

Every surface here is its own process. The menu spawns the chat and
closes on the following line, so a handoff is: one overlay gone, a
quarter of a second of nothing while Python imports and GTK starts and
the pointer is found, then a different overlay. The chat's own entrance
comment already described a continuity the code could not deliver —
"the ring that opened this spiralled into its hub, and a card that
simply blinks on breaks that thread" — and nothing joined the two but
the accident that the mouse had not moved.

This is the thread. The surface being left does not close: it collapses
to the bubble you clicked and holds it lit. The surface arriving is told
where that bubble is, draws a radial.Tether from it to its own hub, and
once the line is taut says so. Only then does the first one let go.

    spawner:  leave(window, ring, index, cmd)   -> holds, then closes
    spawned:  origin()                          -> (x, y, pid) or None
              arrived(pid)                      -> "I have landed"

The origin rides the environment, the way DICTATE_SHOT already hands a
screenshot to the chat. DICTATR_* is the free namespace; DICTATE_* is
the settings REGISTRY's, where a new name would become a config key.

SIGUSR2 carries the acknowledgement. SIGUSR1 and SIGTERM are not
available: on a chat process they already mean commit and discard.

None of this is load-bearing. Without layer-shell there are no screen
coordinates, and an old surface, a failed start or a crash all end the
same way — the hold times out and the spawner closes, exactly as it did
before any of this existed.
"""

import os
import signal
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import GLib, GLibUnix  # noqa: E402

import radial  # noqa: E402

ENV = "DICTATR_FROM"
HOLD_MS = 1500     # stop waiting for a surface that is not coming


def origin():
    """Where this surface was opened from: (x, y, pid), or None.

    Screen coordinates, which on a layer-shell overlay anchored to every
    edge are also window coordinates.
    """
    try:
        x, y, pid = os.environ.get(ENV, "").split(",")
        return float(x), float(y), int(pid)
    except ValueError:
        return None


def arrived(pid):
    """Tell the surface that opened us it can let go."""
    try:
        os.kill(pid, signal.SIGUSR2)
    except OSError:
        pass    # already gone, which is the same outcome


def clear():
    """Forget the origin, so a surface this one spawns does not inherit
    a tether to a window that closed long ago."""
    os.environ.pop(ENV, None)


def leave(window, ring, cmd, index=None, on_release=None, env=None, **popen):
    """Hand off from the bubble at *index* to the surface *cmd* opens.

    Does not close the window. Collapses the ring to the bubble you
    clicked, spawns *cmd* knowing where that bubble is, and waits to be
    told the new surface has landed — or gives up after HOLD_MS, so a
    surface that never starts cannot strand the one that launched it.

    Returns the spawned process.
    """
    if index is None and ring is not None:
        index = ring.chosen
    anchor = (ring.collapse_to(index)
              if ring is not None and index is not None else None)
    child_env = dict(os.environ)
    child_env.pop(ENV, None)
    point = _screen_point(window, ring, anchor)
    if point is not None:
        child_env[ENV] = f"{point[0]:.0f},{point[1]:.0f},{os.getpid()}"
    child_env.update(env or {})

    released = [False]

    def release(*_):
        if not released[0]:
            released[0] = True
            (on_release or _default_release(window, ring))()
        return True     # a signal source that stays installed

    # Before the spawn, never after: an unhandled SIGUSR2 terminates the
    # process, and the child can answer sooner than you would think.
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, release)
    GLib.timeout_add(HOLD_MS, lambda: (release(), False)[1])

    # While it waits, this surface is still a fullscreen overlay. Stop it
    # taking clicks meant for the one arriving on top of it.
    overlay = getattr(window, "ov", None)
    if overlay is not None:
        overlay.freeze_input()
    else:
        radial.clip_input_region(window, ())
    return subprocess.Popen(cmd, env=child_env, **popen)


def arrive(window, canvas, hub, hold_ms=110):
    """Draw the tether back to whatever opened this surface, then cut it.

    Call once the surface is placed and its ring has opened. Attaches the
    line, tells the other side it may let go, waits a beat so both ends
    are seen joined, then releases and removes itself.

    Does nothing when there is no origin — a surface opened by a hotkey,
    by the tray, or from a cold start has nothing to be tethered to, and
    that is the common case.
    """
    src = origin()
    clear()      # one tether per surface, however many rings it opens
    if src is None or canvas is None:
        return None
    ok, bounds = hub.compute_bounds(window)
    if not ok or bounds.size.width <= 0:
        return None
    target = (bounds.origin.x + bounds.size.width / 2,
              bounds.origin.y + bounds.size.height / 2)
    tether = radial.Tether((src[0], src[1]), target)
    tether.set_size_request(window.get_width(), window.get_height())
    canvas.put(tether, 0, 0)

    def cut():
        with_suppressed(lambda: canvas.remove(tether))

    def taut():
        arrived(src[2])
        GLib.timeout_add(hold_ms, lambda: (tether.detach(then=cut), False)[1])

    tether.attach(then=taut)
    return tether


def with_suppressed(fn):
    """Teardown that must not raise: by the time a tether is cut its
    surface may already be closing."""
    try:
        fn()
    except Exception:
        pass


def _default_release(window, ring):
    def release():
        if ring is not None:
            ring.dismiss(then=window.close)
        else:
            window.close()
    return release


def _screen_point(window, ring, anchor):
    """The anchor bubble's centre in screen coordinates, or None.

    None whenever the answer would be a guess: no ring, nothing to
    anchor on, or a widget that has never been allocated — which is also
    the no-layer-shell case, where window coordinates are not screen
    coordinates and there is nothing sensible to tether to.
    """
    if anchor is None or ring is None:
        return None
    ok, bounds = ring.compute_bounds(window)
    if not ok or bounds.size.width <= 0:
        return None
    return bounds.origin.x + anchor[0], bounds.origin.y + anchor[1]
