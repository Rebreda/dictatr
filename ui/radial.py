"""Radial UI kit for dictatr — round bubbles twirling around a hub.

The menu's visual identity, extracted so every surface (menu, onboarding,
downloads) draws from one vocabulary: dark translucent bubbles with thin
white borders, blue hub, green "on" accent, red danger accent.

Pieces:
  Bubble          a spec: icon, tooltip, action, css, children, group
  Hub             what the middle button shows and does
  Ring            hub + satellites, at any count and any depth
  Overlay         a surface that floats where the pointer is
  ProgressBubble  a bubble wearing a progress arc (determinate or spinning)

Every surface uses the same Ring: the menu is one, the chat card hangs
one off its bottom with the microphone as its hub, and the wizard's Back
and Close orbit its step badge. They differ by Style and by the arc they
are given, never by having their own idea of where a button goes. The
geometry itself is next door in radial_layout, with no GTK in it, so it
can be tested rather than looked at.

Submenus: a Bubble with children pushes a level. Every level of the
stack stays on screen, each further out, smaller and dimmer than the one
in front of it, until an orbit passes the style's reach — those are
hidden rather than drawn invisibly. Depth is unbounded. Escape, or the
hub, backs out one level; clicking a parked bubble returns to its level.

`RADIAL_DEMO=layout python3 ui/radial.py` is a live playground for the
layout (item count, grouping, arc, depth); `RADIAL_DEMO=progress` shows
a ProgressBubble cycling through its modes, for stage capture.
"""

import math
import os
import sys
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

# Where the bubbles go lives next door, with no GTK in it so it can be
# tested without a display. Named outright rather than trusted from
# sys.path, the same way the surfaces find this module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion  # noqa: E402
from radial_layout import (  # noqa: E402,F401  (also re-exported)
    ANIM_S, MIN_SPAN_S, OUT_S, PARENT_ALPHA, STAGGER_S, SUB_IN_S,
    SUB_OUT_S, SUB_STAGGER_S, STYLE_CARD, STYLE_MENU, TAU, TWIRL_RAD,
    Arc, Style, ancestor_metrics, arc_avoiding, digit_index, solve,
    timing)


def layer_shell():
    """The layer-shell binding, or None when the surface has to be an
    ordinary window. Needs the library preloaded (the bin/ shims do it;
    it has to load before GTK opens its wayland connection) and a
    compositor that speaks the protocol, which GNOME does not."""
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LS
        return LS if LS.is_supported() else None
    except (ValueError, ImportError):
        return None

# --- palette (the whole family paints with these) ----------------------
CHARCOAL = "#1c1d22"   # bubble fill
INK = "#e8eaf1"        # icons, text
BLUE = "#8ab4f8"       # hub, focus, progress
GREEN = "#81c995"      # live / on
RED = "#f28b82"        # danger / close

# --- geometry ----------------------------------------------------------
# How big a ring is belongs to its Style (see radial_layout), because it
# is a property of the surface the ring is on and not of the kit. What
# is left here is the progress arc, which is drawn rather than laid out.
ARC_PAD = 5
ARC_W = 3.0

CSS = f"""
window {{ background: transparent; }}
.hub, .bubble {{
  border-radius: 9999px;
  border: 1px solid alpha(#ffffff, 0.10);
  background: alpha({CHARCOAL}, 0.93);
  transition: background 130ms ease, border-color 130ms ease;
}}
.hub image {{ color: {BLUE}; }}
.bubble image {{ color: {INK}; }}
.bubble.parent {{ border-color: alpha(#ffffff, 0.06); }}
.bubble.parent:hover {{ border-color: alpha({BLUE}, 0.5); }}
.bubble:hover, .bubble:focus-visible {{
  background: alpha({BLUE}, 0.28);
  border-color: alpha({BLUE}, 0.6);
}}
.bubble.on {{ background: alpha({GREEN}, 0.25); border-color: alpha({GREEN}, 0.6); }}
.bubble.on image {{ color: {GREEN}; }}
.hub:hover {{ background: alpha({RED}, 0.25); }}

/* The vocabulary the surfaces share. A card of message pills with a hub
   and satellites under it is what the chat, the wizard and any later
   surface all are, and each used to re-declare these with small drifts
   (the same status pill was 11px and round in one file, 12px and boxy
   in another). Anything genuinely local to one surface still lives
   there; this is only what more than one of them needs. */
/* A hub a surface supplies itself (the chat's microphone, the wizard's
   step badge). It carries no size: how big a hub is comes from the
   ring's Style, and a min-width here would quietly win over it. */
.hubbtn {{
  border-radius: 9999px;
  border: 1px solid alpha(#ffffff, 0.10);
  background: alpha({CHARCOAL}, 0.93);
  min-width: 0; min-height: 0; padding: 0;
  transition: background 150ms ease, border-color 150ms ease;
}}
.hubbtn image {{ color: {BLUE}; }}
.hubbtn:hover {{ border-color: alpha(#ffffff, 0.35); }}
.hubbtn.rec {{ background: alpha({GREEN}, 0.25); border-color: alpha({GREEN}, 0.6); }}
.hubbtn.rec image {{ color: {GREEN}; }}
/* Nothing to press right now (the model is answering): say so, rather
   than taking the click and doing nothing with it. */
.hubbtn:disabled {{ background: alpha({CHARCOAL}, 0.6); }}
.hubbtn:disabled image {{ color: alpha({INK}, 0.30); }}
.msg {{
  border-radius: 20px; padding: 9px 14px;
  background: alpha({CHARCOAL}, 0.93);
  border: 1px solid alpha(#ffffff, 0.10);
  color: {INK};
  transition: border-color 150ms ease;
}}
.msg-user {{ border-color: alpha({GREEN}, 0.45); }}
.msg-user.live {{ border-color: alpha({GREEN}, 0.85); }}
.msg-ai {{ border-color: alpha({BLUE}, 0.35); }}
.msg .title {{ font-size: 15px; font-weight: 700; color: {INK}; }}
.msg .body {{ color: alpha({INK}, 0.80); }}
.status-pill {{
  background: alpha({CHARCOAL}, 0.85);
  border: 1px solid alpha(#ffffff, 0.08);
  border-radius: 9999px; padding: 4px 13px;
  color: alpha({INK}, 0.62); font-size: 11px;
}}
.status-pill.error, .status-pill.bad {{
  color: {RED}; border-color: alpha({RED}, 0.45);
}}
.status-pill.good {{ color: {GREEN}; border-color: alpha({GREEN}, 0.45); }}
/* Going back is not a destructive act, so it does not wear the
   danger colour the closing hub does. */
.hub.back:hover {{ background: alpha({BLUE}, 0.25); }}
.hub.back:hover {{ background: alpha({BLUE}, 0.28); }}
""".encode()

# Our own symbolic icons, laid out as an icon theme. Desktop themes are
# a lottery at large sizes (Breeze's 32px input-keyboard-symbolic is a
# full-color icon, which GTK masks into a solid blob), and these names
# are shared by the menu and the wizard.
ICON_PATH = Path(__file__).resolve().parent / "icons" / "theme"
CHECK_ICON = "dictatr-check-symbolic"

_css_applied = False


def apply_css(extra: bytes = b""):
    """Install the kit stylesheet (once), register the bundled icon
    theme, and add optional caller CSS."""
    global _css_applied
    display = Gdk.Display.get_default()
    if not _css_applied:
        Gtk.IconTheme.get_for_display(display).add_search_path(
            str(ICON_PATH))
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _css_applied = True
    if extra:
        provider = Gtk.CssProvider()
        provider.load_from_data(extra)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


_ease_out = motion.ease_out
_clamp = motion.clamp


# --- the animator -------------------------------------------------------
# The GTK half of ui/motion.py: one frame-clock loop, and the handful of
# named moves the surfaces actually need. Before this the ring had a real
# animation engine and every other surface set values in a single frame,
# which is why an answer arrived as a jump and a pill was destroyed
# rather than faded. Nothing outside this file should write a tick
# callback of its own.

def drive(widget, duration, update, done=None):
    """Call *update(t)* every frame for *duration* seconds, then *done*.

    Generation-counted per widget, so starting a second animation on the
    same widget cancels the first instead of letting the two fight over
    the same property.

    A widget with no frame clock — not realised, or on a surface that has
    not been mapped — gets its final state applied at once. Motion is a
    way of arriving somewhere, and somewhere is where it must end up even
    when nobody can watch it get there. It is also what lets the check
    tools drive these surfaces without presenting a window.
    """
    gen = getattr(widget, "_anim_gen", 0) + 1
    widget._anim_gen = gen
    if widget.get_frame_clock() is None:
        update(duration)
        if done is not None:
            done()
        return

    t0 = [None]

    def tick(_w, clock):
        if getattr(widget, "_anim_gen", 0) != gen:
            return False
        now = clock.get_frame_time() / 1e6
        if t0[0] is None:
            t0[0] = now
        t = min(now - t0[0], duration)
        update(t)
        if t >= duration:
            if done is not None:
                done()
            return False
        return True

    widget.add_tick_callback(tick)


def play(widget, timeline, apply, done=None):
    """Run a motion.Timeline, handing each frame's values to *apply*."""
    drive(widget, timeline.duration, lambda t: apply(timeline.at(t)), done)


def fade(widget, to, duration=0.20, ease=motion.ease_out, done=None,
         delay=0.0):
    track = motion.Track(widget.get_opacity(), to, duration, delay=delay,
                         ease=ease)
    drive(widget, track.end,
          lambda t: widget.set_opacity(track.at(t)), done)


def grow(widget, frm, to, duration=0.22, done=None):
    """Move a widget's height request from *frm* to *to*, then release it.

    For a pill whose text has just changed: the natural height jumps in
    one frame and shoves everything above it. Holding the old height and
    easing to the new one is the difference between an answer arriving
    and an answer appearing.
    """
    track = motion.Track(frm, to, duration)

    def finish():
        widget.set_size_request(-1, -1)   # back to natural height
        if done is not None:
            done()

    drive(widget, duration,
          lambda t: widget.set_size_request(-1, round(track.at(t))), finish)


def crossfade(widget, apply, duration=0.18, done=None):
    """Fade *widget* down, call *apply*, fade it back up.

    For text that replaces text — a status line, a step count — where a
    hard swap also changes the widget's width and shifts whatever is
    centred beside it.
    """
    start = widget.get_opacity() or 1.0
    half = duration / 2
    out = motion.Track(start, 0.0, half, ease=motion.ease_in)
    back = motion.Track(0.0, start, half, delay=half)
    switched = [False]

    def update(t):
        if t < half and not switched[0]:
            widget.set_opacity(out.at(t))
            return
        if not switched[0]:
            switched[0] = True
            apply()
        widget.set_opacity(back.at(t))

    drive(widget, duration, update, done)


def scroll_to(widget, adjustment, value, duration=0.25):
    """Ease a scroll instead of jumping it."""
    track = motion.Track(adjustment.get_value(), value, duration)
    drive(widget, duration, lambda t: adjustment.set_value(track.at(t)))


def fade_out_then(widget, done, duration=0.18, delay=0.0):
    """Fade a widget away and hand it back once it has actually gone.

    Pills used to be removed outright while the revealer that faded them
    in was never asked to fade them out.
    """
    fade(widget, 0.0, duration, ease=motion.ease_in, done=done, delay=delay)


class Bubble:
    """One satellite: icon + tooltip + either an action or children.

    css lists extra style classes ("on" paints it green). A bubble with
    children opens a submenu instead of firing an action; those children
    may be a callable, so a level that is expensive to work out (the
    model's shortlist for a message) is only worked out if you open it.

    key names the bubble across rebuilds, so a ring can be updated in
    place instead of re-spiralled. group says which of its neighbours it
    belongs with: the layout leaves more room between groups than
    inside them, which is how "back and close" reads as a pair rather
    than as two of four evenly spaced buttons.
    """

    def __init__(self, icon, tooltip, action=None, css=(), children=None,
                 key=None, group="", shown=True):
        self.icon = icon
        self.tooltip = tooltip
        self.action = action
        self.css = tuple(css)
        self.children = children if callable(children) else (
            list(children) if children else None)
        self.key = key
        self.group = group
        self.shown = shown


class Hub:
    """The button in the middle: what it shows and what it does.

    *widget* lets a surface hand in a button it already owns — the chat
    card's microphone is the hub of its ring, and it is the card, not
    the ring, that decides what a microphone looks like right now.

    *keep* is for exactly that case. By default the hub becomes a Back
    control inside a submenu, which is right when the hub is a Close
    button; it is wrong when the hub is the one control the surface
    exists for, and a card whose microphone turned into an arrow every
    time you opened a menu would be a card you could not talk to.
    """

    def __init__(self, icon, tooltip="", action=None, css=(),
                 sensitive=True, widget=None, keep=False):
        self.icon = icon
        self.tooltip = tooltip
        self.action = action
        self.css = tuple(css)
        self.sensitive = sensitive
        self.widget = widget
        self.keep = keep


class _Level:
    """One ring of items, the buttons drawn for them, and its geometry.

    *items* is already folded: anything that could not fit on the arc at
    the smallest allowed size has been moved into a trailing "More"
    bubble, so index arithmetic here never has to know about overflow.
    """

    __slots__ = ("items", "buttons", "metrics", "hub")

    def __init__(self, items, buttons, metrics, hub):
        self.items = items
        self.buttons = buttons
        self.metrics = metrics
        self.hub = hub

    def visible(self):
        """(item, button) pairs that are actually on the ring, in order."""
        return [(it, b) for it, b in zip(self.items, self.buttons) if it.shown]


class Ring(Gtk.Fixed):
    """The radial circle: a hub with satellites on an orbit.

    Position it like any widget (menu.py drops it on a fullscreen canvas
    at the pointer, chat.py hangs it under its card). Call open() once
    placed; dismiss(then=...) spirals everything back in and then calls
    through. handle_key() consumes Escape (back one level; False at root
    so the caller can close) and the number keys 1-9.

    Where anything goes is decided in one place — _pose() says where
    every button belongs given the current stack of levels, and _glide()
    moves whatever is on screen to that. Opening, dismissing, entering a
    submenu, leaving one, revealing an item, or the arc narrowing
    because the card above grew, are all the same operation. That is
    what makes a new surface free: it supplies items, a style and an
    arc, and never any layout.

    Every level of the stack is drawn, each one further out, smaller and
    dimmer than the one in front of it, until an orbit would pass the
    style's reach — those are hidden outright rather than drawn at an
    invisible opacity, because a transparent button still takes clicks.
    Depth itself is unbounded; only how much of it you can see is not.
    """

    def __init__(self, items, hub_icon="window-close-symbolic",
                 hub_tooltip="Close  [Esc]", on_root_hub=None, *,
                 hub=None, style=STYLE_MENU, arc=None, obstacles=None,
                 on_geometry=None):
        super().__init__()
        self.style = style
        self.on_root_hub = on_root_hub
        self.on_geometry = on_geometry
        self._arc = arc                  # Arc, or a callable returning one
        self._obstacles = obstacles      # callable -> iterable of widgets
        self._gen = 0                    # bumping this cancels a glide
        self._navigating = False
        self._closed = False
        self._chosen = None
        self._reported = 0.0
        self._rendered = {}              # widget -> (angle, radius, alpha, size)
        self._applied = {}               # widget -> the size it is wearing
        self.levels = []

        # One fixed box, sized for the deepest orbit the style allows.
        # The old ring was 250px square while its own parent orbit
        # needed 341, so ancestors fell outside the widget and, with it,
        # outside the input region: painted, hovering, unclickable.
        self._side = 2 * style.max_reach
        self._cx = self._cy = self._side / 2
        self.set_size_request(round(self._side), round(self._side))

        root = hub or Hub(hub_icon, hub_tooltip)
        self.hub = self._make_hub(root)
        self._hub_spec = root

        # A progress arc around the hub, for rings that front a long
        # operation. Drawn in the same Fixed so it tracks the hub, and
        # never a target so clicks pass through.
        self._fraction = 0.0
        self._spinning = False
        side = style.hub + 2 * ARC_PAD
        self._arc_area = Gtk.DrawingArea()
        self._arc_area.set_size_request(round(side), round(side))
        self._arc_area.set_can_target(False)
        self._arc_area.set_draw_func(self._draw_arc)
        self.put(self._arc_area, self._cx - side / 2, self._cy - side / 2)

        self.levels.append(self._build(items, root))
        self._apply(self._collapsed(self._pose()))

    # --- the hub --------------------------------------------------------
    def _make_hub(self, spec):
        btn = spec.widget
        if btn is None:
            btn = Gtk.Button(icon_name=spec.icon)
            btn.add_css_class("hub")
            btn.set_size_request(round(self.style.hub), round(self.style.hub))
            btn.set_focusable(False)   # same reason as the satellites
        for c in spec.css:
            btn.add_css_class(c)
        btn.set_tooltip_text(spec.tooltip)
        btn.set_opacity(0.0)
        btn.connect("clicked", self._on_hub)
        self.put(btn, self._cx - self.style.hub / 2,
                 self._cy - self.style.hub / 2)
        return btn

    def set_hub(self, icon=None, tooltip=None, sensitive=None, css=None):
        """Relabel the hub in place, for a surface whose state changes
        without the ring changing (listening, then thinking, then done)."""
        if icon:
            self.hub.set_icon_name(icon)
        if tooltip is not None:
            self.hub.set_tooltip_text(tooltip)
        if sensitive is not None:
            self.hub.set_sensitive(sensitive)
        if css is not None:
            for c in ("on", "rec", "back", "danger"):
                self.hub.remove_css_class(c)
            for c in css:
                self.hub.add_css_class(c)

    def _on_hub(self, _btn):
        if len(self.levels) > 1 and not self._hub_spec.keep:
            self.back()
        elif self._hub_spec.action is not None:
            self._hub_spec.action()
        elif self.on_root_hub is not None:
            self.on_root_hub()

    # --- building a level ----------------------------------------------
    def _build(self, items, hub):
        """Fold what will not fit, make the buttons, solve the geometry."""
        items = self._fold(list(items))
        depth = len(self.levels)
        buttons = [self._make_button(it, depth, i)
                   for i, it in enumerate(items)]
        level = _Level(items, buttons, None, hub)
        level.metrics = self._solve(level)
        return level

    def _fold(self, items):
        """Move the tail that cannot fit into a submenu of its own.

        A second orbit would double the input region and put two bubbles
        in the same direction from the hub, which is the one thing a
        radial menu is for. A ring that can nest arbitrarily deep can
        afford to nest here instead.
        """
        shown = [it for it in items if it.shown]
        metrics = solve([it.group for it in shown], self._arc_for(None),
                        self.style)
        if not metrics.overflow:
            return items
        keep = len(shown) - metrics.overflow
        tail = shown[keep:]
        head = [it for it in items if it not in tail]
        more = Bubble("view-more-symbolic", f"{len(tail)} more",
                      children=tail,
                      group=head[-1].group if head else "")
        return head + [more]

    def _make_button(self, item, depth, index):
        btn = Gtk.Button()
        img = Gtk.Image(icon_name=item.icon)
        btn.set_child(img)
        btn.add_css_class("bubble")
        for c in item.css:
            btn.add_css_class(c)
        # Not focusable on purpose: a focused button activates on space
        # or Return, so a ring that mapped under a stray keypress would
        # fire an action nobody chose. The ring is driven by the pointer
        # and by handle_key's number keys.
        btn.set_focusable(False)
        btn.set_opacity(0.0)
        btn._img = img
        btn._at = (depth, index)
        btn.connect("clicked", self._on_button)
        self.put(btn, self._cx, self._cy)
        return btn

    # --- geometry --------------------------------------------------------
    def _obstacle_rects(self):
        """What is in the way, in this widget's own coordinates.

        Widgets, or plain (x, y, w, h) rects for a surface that already
        knows its own geometry. A widget that has never been allocated
        measures as nothing and is skipped, which is what leaves an
        unrealised surface with the whole circle to play with instead of
        a layout solved against zeroes.
        """
        rects = []
        for obstacle in (self._obstacles() if self._obstacles else ()):
            if obstacle is None:
                continue
            if isinstance(obstacle, tuple):
                rects.append(obstacle)
                continue
            if not obstacle.get_visible():
                continue
            ok, b = obstacle.compute_bounds(self)
            if ok and b.size.width > 0 and b.size.height > 0:
                rects.append((b.origin.x, b.origin.y,
                              b.size.width, b.size.height))
        return rects

    def _arc_for(self, level, radius=None):
        """The sweep this level may use.

        A fixed arc if the surface named one. Otherwise the largest gap
        left by the widgets the surface says are in the way — which is
        how the chat card's chrome ends up under the conversation
        without anybody writing down an angle.
        """
        if self._arc is not None:
            return self._arc() if callable(self._arc) else self._arc
        rects = self._obstacle_rects()
        if not rects:
            return Arc.full()
        return arc_avoiding(
            (self._cx, self._cy), radius or self.style.radius,
            self.style.bubble / 2 + self.style.gap / 2, rects,
            bounds=(0, 0, self._side, self._side))

    def _solve(self, level):
        groups = [it.group for it, _b in level.visible()]
        arc = self._arc_for(level)
        metrics = solve(groups, arc, self.style)
        if self._arc is None and self._obstacles is not None:
            # The arc depends on the radius, which depends on the arc.
            # Two passes and stop: this converges in practice and a loop
            # here would be a loop in a frame callback.
            metrics = solve(groups, self._arc_for(level, metrics.radius),
                            self.style)
        return metrics

    def _pose(self):
        """Where everything belongs, given the current stack of levels."""
        pose = {self.hub: (0.0, 0.0, 1.0, self.style.hub)}
        top = len(self.levels) - 1
        live = self.levels[top].metrics
        for depth, level in enumerate(self.levels):
            orbit = ancestor_metrics(live, top - depth, self.style)
            if orbit is None:
                continue      # deeper than the style will draw: not shown
            for (_it, btn), angle in zip(level.visible(), level.metrics.angles):
                pose[btn] = (angle, orbit.radius, orbit.alpha, orbit.bubble)
        return pose

    @staticmethod
    def _collapsed(pose):
        """The same arrangement, spiralled into the hub and invisible."""
        return {w: (a, 0.0, 0.0, s) for w, (a, _r, _al, s) in pose.items()}

    @property
    def size(self):
        """The square side the ring is drawn in, at any depth."""
        return self._side

    @property
    def extent(self):
        """How far from the hub the ring currently reaches.

        The box is sized for the deepest level the style allows; this is
        what is actually being drawn right now. A surface the ring hangs
        off needs it, because a ring that has just grown a level has to
        be made room for rather than drawn over the card above it.
        """
        return max((r + s / 2 for _a, r, al, s in self._rendered.values()
                    if al > 0.01), default=self.style.hub / 2)

    @property
    def depth(self):
        return len(self.levels) - 1

    def hit_widgets(self):
        """Everything that should take a click, for clip_input_region.

        Per-bubble, not one square: the widget's own box is sized for
        the deepest orbit the style allows, and claiming all of it would
        take input across a region the ring never paints.
        """
        return [self.hub, *(w for w, (_a, _r, al, _s) in self._rendered.items()
                            if al > 0.05 and w.get_visible())]

    # --- drawing ---------------------------------------------------------
    def _apply(self, pose, settle=False):
        for w in self._rendered:
            if w not in pose:
                # Hidden, or on a level too deep to draw. Park it on the
                # hub and take it out of the picture: an invisible
                # widget still takes clicks, and a stale one would take
                # them where nothing is painted.
                w.set_opacity(0.0)
                w.set_visible(False)
                self.move(w, self._cx, self._cy)
        self._rendered = pose
        for w, (angle, radius, alpha, size) in pose.items():
            if abs(self._applied.get(w, -1) - size) > 0.5:
                self._applied[w] = size
                w.set_size_request(round(size), round(size))
                img = getattr(w, "_img", None)
                if img is not None:
                    img.set_pixel_size(round(size * self.style.icon_ratio))
            self.move(w, self._cx + radius * math.cos(angle) - size / 2,
                      self._cy + radius * math.sin(angle) - size / 2)
            w.set_opacity(alpha)
            if settle:
                # An invisible widget still takes clicks, so a level the
                # style will not draw has to be genuinely gone.
                w.set_visible(alpha > 0.01)
            elif alpha > 0.01:
                w.set_visible(True)

    def _glide(self, target, duration, stagger=0.0, twirl=0.0, done=None,
               drop=()):
        """Move whatever is on screen to *target*, then call *done*.

        Every arrangement change goes through here, and every one starts
        from where things actually are rather than from a nominal state,
        so an interrupted animation lands somewhere valid instead of
        needing a state machine to forbid the interruption.
        """
        start = self._rendered
        order = [*target, *(w for w in start if w not in target)]
        moves = {}
        for w in order:
            frm = start.get(w)
            to = target.get(w)
            if frm is None:
                frm = (to[0], 0.0, 0.0, to[3])
            if to is None:
                to = (frm[0], 0.0, 0.0, frm[3])
            moves[w] = (frm, to)
        slot = {w: i for i, w in enumerate(order)}
        duration, stagger = timing(len(order), duration, stagger)
        span = max(duration - (len(order) - 1) * stagger, MIN_SPAN_S)

        def update(t):
            pose = {}
            for w, (frm, to) in moves.items():
                e = _ease_out(_clamp((t - slot[w] * stagger) / span))
                delta = (to[0] - frm[0] + math.pi) % TAU - math.pi
                pose[w] = (frm[0] + delta * e + (1 - e) * twirl,
                           frm[1] + (to[1] - frm[1]) * e,
                           frm[2] + (to[2] - frm[2]) * e,
                           frm[3] + (to[3] - frm[3]) * e)
            self._apply(pose)

        def finish():
            self._apply(target, settle=True)
            for w in drop:
                self.remove(w)
                self._applied.pop(w, None)
            if done is not None:
                done()

        self._drive(duration, update, finish)

    def _drive(self, duration, update, done=None):
        drive(self, duration, update, done)

    # --- lifecycle --------------------------------------------------------
    def open(self):
        """Twirl the root ring out of the hub."""
        self._retitle()
        self._glide(self._pose(), ANIM_S, STAGGER_S, twirl=TWIRL_RAD)

    def dismiss(self, then=None):
        """Spiral everything back into the hub, fading, then call through."""
        if self._closed:
            if then is not None:
                then()
            return
        self._closed = True
        self._glide(self._collapsed(self._pose()), OUT_S, 0.02,
                    twirl=TWIRL_RAD, done=then)

    def collapse_to(self, index, done=None):
        """Spiral every bubble but one into the hub, and hold.

        The waiting state of a handoff: you clicked something, the
        surface it opens is a cold process away, and the bubble you
        clicked stays lit where it is so the new surface has something
        to reach back to. Returns where that bubble sits, in this
        widget's coordinates, or None if there is nothing to anchor on.
        """
        level = self.levels[-1]
        if not 0 <= index < len(level.items) or not level.items[index].shown:
            return None
        keep = level.buttons[index]
        pose = self._pose()
        anchor = pose.get(keep)
        if anchor is None:
            return None
        held = {self.hub: (0.0, 0.0, 0.0, self.style.hub), keep: anchor}
        self._glide(held, SUB_OUT_S, 0.02, twirl=TWIRL_RAD, done=done)
        angle, radius, _alpha, size = anchor
        return (self._cx + radius * math.cos(angle),
                self._cy + radius * math.sin(angle), size)

    def relayout(self, animate=True):
        """Re-solve every level and move to it — after the arc changed,
        because whatever was in the ring's way grew or went away."""
        if self._closed:
            return
        for level in self.levels:
            level.metrics = self._solve(level)
        self._report_extent()
        pose = self._pose()
        if animate:
            self._glide(pose, SUB_IN_S, 0.0)
        else:
            self._apply(pose, settle=True)

    # --- navigation -------------------------------------------------------
    @property
    def chosen(self):
        """The item index the ring is acting on, for whatever the action
        needs to know about the bubble that started it — a handoff has to
        tether back to the one you clicked."""
        return self._chosen

    def activate(self, index):
        if self._closed:
            return
        level = self.levels[-1]
        if not 0 <= index < len(level.items):
            return
        self._chosen = index
        item = level.items[index]
        children = item.children() if callable(item.children) else item.children
        if children:
            self.push(children, Hub("go-previous-symbolic",
                                    f"Back to {item.tooltip}  [Esc]",
                                    css=("back",)))
        elif item.action is not None:
            item.action()

    def push(self, items, hub=None):
        """Open a level below the live one. Any depth, any number of times."""
        if self._closed or self._navigating:
            return
        self._navigating = True
        self.levels.append(self._build(items, hub or self._hub_spec))
        self._enter()

    def back(self):
        """Leave the live level for the one it came from."""
        self.go_to(len(self.levels) - 2)

    def go_to(self, depth, index=None):
        """Return to *depth*, then run its item *index* if there is one.

        Clicking a parked ancestor comes here. The old ring stored an
        index that was local to its level but popped exactly one level
        before using it, so clicking a grandparent fired whatever the
        parent happened to have at that position; carrying the depth
        along with the index is the whole fix.
        """
        if self._closed or depth < 0 or depth >= len(self.levels):
            return
        dropped = []
        while len(self.levels) > depth + 1:
            dropped.extend(self.levels.pop().buttons)
        level = self.levels[-1]
        target = None
        if index is not None and 0 <= index < len(level.items):
            item = level.items[index]
            # The item you clicked to get here is the way back, not a
            # round trip through the level you just left.
            if not item.children:
                target = index
        self._navigating = False
        self._enter(drop=dropped)
        if target is not None:
            # Now, not when the glide lands: an action that waits on an
            # animation is an action that never happens on a surface
            # whose frame clock is not running yet.
            self.activate(target)

    def _report_extent(self):
        """Tell the surface how much room the ring now needs.

        Only on a real change, and after the levels have been solved, so
        a card can move out of the way in the same beat the ring grows
        rather than being drawn over.
        """
        if self.on_geometry is None:
            return
        wanted = max((ancestor_metrics(self.levels[-1].metrics, back,
                                       self.style)
                      for back in range(len(self.levels))),
                     key=lambda o: o.radius + o.bubble / 2 if o else 0,
                     default=None)
        reach = (wanted.radius + wanted.bubble / 2 if wanted
                 else self.style.hub / 2)
        if abs(reach - self._reported) > 0.5:
            self._reported = reach
            self.on_geometry(reach)

    def _enter(self, drop=(), then=None):
        """Settle onto the current stack: re-solve, retitle, glide."""
        for level in self.levels:
            level.metrics = self._solve(level)
        self._retitle()
        self._report_extent()
        # The flag only exists to swallow a second click during the hop,
        # and it is cleared on a timer rather than when the glide lands
        # so that a ring whose frame clock is not running (a surface
        # built but not yet mapped) cannot get stuck mid-navigation.
        GLib.timeout_add(int(SUB_IN_S * 1000) + 50, self._settled)
        self._glide(self._pose(), SUB_IN_S, SUB_STAGGER_S,
                    twirl=TWIRL_RAD / 2, done=then, drop=drop)

    def _settled(self):
        self._navigating = False
        return False

    def swap(self, items, hub_icon=None, hub_tooltip=None, forward=True,
             done=None, hub=None):
        """Replace the live level's items with a different set.

        Unlike a submenu this keeps no stack, because what it renders is
        a sibling of what was there, not a child: the wizard walks its
        steps with it, and the menu uses it to drop the model's
        shortlist in over the catalogue it opened with.
        """
        if self._closed:
            return
        spec = hub or self.levels[-1].hub
        if hub_icon or hub_tooltip:
            spec = Hub(hub_icon or spec.icon, hub_tooltip or spec.tooltip,
                       spec.action, spec.css, spec.sensitive, spec.widget,
                       spec.keep)
        old = self.levels.pop()
        self.levels.append(self._build(items, spec))
        self._enter(drop=old.buttons, then=done)

    def update(self, items):
        """Rewrite the live level in place when only its faces changed.

        Matched by Bubble.key: same keys in the same order means the
        icons, tooltips, classes and actions are refreshed and nothing
        moves. A toggle that flips one bubble green used to re-spiral
        the entire ring to say so.
        """
        items = list(items)
        level = self.levels[-1]
        keys = [it.key for it in level.items]
        if None in keys or keys != [it.key for it in items]:
            self.swap(items)
            return
        moved = False
        for old, new, btn in zip(level.items, items, level.buttons):
            btn._img.set_from_icon_name(new.icon)
            for c in old.css:
                btn.remove_css_class(c)
            for c in new.css:
                btn.add_css_class(c)
            moved = moved or old.shown != new.shown
        level.items = items
        self._retitle()
        if moved:
            self.relayout()

    def set_item_shown(self, key, shown, animate=True):
        """Show or hide one item by key; the rest close the gap.

        Any level, not just the live one: what makes an item worth
        showing usually happens elsewhere and on its own schedule, and
        an answer that arrives while a submenu is open must not be lost
        because the ring happened to be looking at something else.
        """
        for level in self.levels:
            for item in level.items:
                if item.key == key and item.shown != shown:
                    item.shown = shown
                    self._retitle()
                    self.relayout(animate)
                    return

    def _retitle(self):
        """Number the live level's bubbles, and name the parked ones for
        what clicking them does."""
        top = len(self.levels) - 1
        for depth, level in enumerate(self.levels):
            live = depth == top
            for i, (item, btn) in enumerate(level.visible()):
                if live:
                    hint = f"  [{i + 1}]" if i < 9 else ""
                    btn.set_tooltip_text(f"{item.tooltip}{hint}")
                    btn.remove_css_class("parent")
                else:
                    btn.set_tooltip_text(f"Back to {item.tooltip}")
                    btn.add_css_class("parent")
        spec = self.levels[-1].hub
        if not self._hub_spec.keep:
            self.set_hub(spec.icon, spec.tooltip, css=spec.css)

    def _on_button(self, btn):
        depth, index = btn._at
        if depth == len(self.levels) - 1:
            self.activate(index)
        else:
            self.go_to(depth, index)

    # --- hub progress ------------------------------------------------------
    def set_fraction(self, fraction):
        self._fraction = _clamp(fraction)
        self._spinning = False
        self._arc_area.queue_draw()

    def set_indeterminate(self, spinning=True):
        if spinning and not self._spinning:
            self._spinning = True
            self._arc_area.add_tick_callback(
                lambda *_: (self._arc_area.queue_draw(), self._spinning)[1])
        elif not spinning:
            self._spinning = False
            self._arc_area.queue_draw()

    def _draw_arc(self, _area, cr, w, h):
        if not self._spinning and self._fraction <= 0:
            return
        cx, cy = w / 2, h / 2
        r = self.style.hub / 2 + ARC_PAD - ARC_W / 2
        cr.set_line_width(ARC_W)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(0x8a / 255, 0xb4 / 255, 0xf8 / 255, 1.0)
        if self._spinning:
            phase = (GLib.get_monotonic_time() / 1e6) * 1.6 * math.pi
            cr.arc(cx, cy, r, phase, phase + 0.55 * math.pi)
        else:
            top = -math.pi / 2
            cr.arc(cx, cy, r, top, top + 2 * math.pi * self._fraction)
        cr.stroke()

    # --- keyboard -----------------------------------------------------------
    def handle_key(self, keyval):
        """Escape backs out one level (False at root: caller closes);
        number keys activate the visible ring."""
        if keyval == Gdk.KEY_Escape:
            if len(self.levels) > 1:
                self.back()
                return True
            return False
        visible = self.levels[-1].visible()
        i = digit_index(keyval, len(visible))
        if i is None:
            return False
        self.activate(self.levels[-1].items.index(visible[i][0]))
        return True


def clip_input_region(window, widgets) -> bool:
    """Take input only where something is painted.

    A fullscreen overlay that accepted clicks everywhere would hold the
    desktop hostage while it floats over it. Used by every overlay that
    is meant to be see-through: the wizard centres its card and the chat
    follows the pointer, but both want the same thing everywhere they
    are not drawn. Always returns True, to be a GLib timeout directly.
    """
    surface = window.get_surface()
    if surface is None:
        return True
    region = cairo.Region()
    for widget in widgets:
        ok, b = widget.compute_bounds(window)
        if ok and b.size.width > 0 and widget.get_visible():
            region.union(cairo.RectangleInt(
                int(b.origin.x) - 4, int(b.origin.y) - 4,
                int(b.size.width) + 8, int(b.size.height) + 8))
    surface.set_input_region(region)
    return True


class Tether(Gtk.DrawingArea):
    """The line that joins two surfaces while one hands over to the other.

    A handoff is a new process: a quarter of a second in which the thing
    you clicked is gone and the thing you asked for is not there yet.
    This is what spans it — a tapered band drawn from the bubble you
    clicked to the hub of the surface that answered, thick where it
    leaves and narrowing to a point where it arrives.

    It hangs slack while it is reaching and pulls straight as it lands,
    then releases from the far end and is drawn into the hub. Takes no
    input: it is a thread between two surfaces, not a control.
    """

    W0 = 15.0          # width where it leaves the origin bubble
    W1 = 2.0           # width where it meets the hub
    SLACK = 26.0       # how far it bows before it goes taut
    ATTACH_S = 0.16
    DETACH_S = 0.22

    def __init__(self, origin, target):
        super().__init__()
        self.origin = origin
        self.target = target
        self.set_can_target(False)
        self.set_draw_func(self._draw)
        self._extent = 0.0     # how far along the curve it has reached
        self._start = 0.0      # how much of the origin end has let go
        self._slack = 1.0
        self.set_opacity(0.0)

    def _draw(self, _area, cr, _w, _h):
        # The released end walks toward the target, so a detaching
        # tether is drawn from further and further along its own curve.
        p0 = motion.bezier(self.origin, self.target,
                           self.SLACK * self._slack, self._start)
        points = motion.taper(p0, self.target, self.W0 * (1 - self._start),
                              self.W1, self.SLACK * self._slack,
                              self._extent)
        if not points:
            return
        cr.move_to(*points[0])
        for x, y in points[1:]:
            cr.line_to(x, y)
        cr.close_path()
        cr.set_source_rgba(0x8a / 255, 0xb4 / 255, 0xf8 / 255, 0.55)
        cr.fill()

    def attach(self, done=None):
        """Reach from the origin to the target, and pull taut."""
        line = motion.Timeline(
            alpha=motion.Track(0.0, 1.0, 0.08),
            extent=motion.Track(0.0, 1.0, self.ATTACH_S),
            slack=motion.Track(1.0, 0.0, self.ATTACH_S * 1.4,
                               ease=motion.ease_in_out))

        def apply(v):
            self.set_opacity(v["alpha"])
            self._extent = v["extent"]
            self._slack = v["slack"]
            self.queue_draw()

        play(self, line, apply, done)

    def detach(self, done=None):
        """Let go of the origin end and be drawn into the hub."""
        line = motion.Timeline(
            start=motion.Track(0.0, 1.0, self.DETACH_S,
                               ease=motion.ease_in),
            alpha=motion.Track(self.get_opacity(), 0.0, self.DETACH_S,
                               delay=self.DETACH_S * 0.4))

        def apply(v):
            self._start = v["start"]
            self.set_opacity(v["alpha"])
            self.queue_draw()

        play(self, line, apply, done)


class Overlay:
    """A surface that floats where the pointer is.

    The menu, the voice chat and the wizard are all the same object
    underneath: a fullscreen transparent layer-shell window with one
    child placed at the cursor, which can be dragged and which may let
    clicks through everywhere it is not painted. Each carried its own
    copy of that, and the copies had already drifted.

    The window keeps its own behaviour: what the child is, whether a
    click outside dismisses it, what counts as a drag handle. This owns
    only the parts that are the same either way.

        self.ov = Overlay(self, child, (w, h))
        self.ov.start()          # layer shell, canvas, find the pointer

    *hit_widgets* makes the surface click-through: only those widgets
    take input and the desktop underneath keeps the rest. Leave it None
    for a surface that should swallow every click (the menu does, so
    that clicking away dismisses it).
    """

    CLAMP = 0        # keep the child this far from the screen edge
    DRAG_SLOP = 8    # movement below this is still a click

    def __init__(self, window, child, size, hit_widgets=None,
                 on_place=None, clamp=0, anchor=(0.5, 0.5)):
        self.win = window
        self.child = child
        self.w, self.h = size
        self.hit_widgets = hit_widgets
        self.on_place = on_place
        self.clamp = clamp
        self.anchor = anchor
        self.canvas = None
        self.placed = False
        self.pos = (0, 0)
        self._floating = []
        self._frozen = False
        self._drag_from = None
        self._drag_test = None
        self._polls = 0
        self._ticks = 0
        self._armed = False

    # --- setup ---------------------------------------------------------
    def start(self):
        """Make the window an overlay. False when layer-shell is absent,
        and the caller should fall back to an ordinary window."""
        ls = layer_shell()
        if ls is None:
            return False
        ls.init_for_window(self.win)
        ls.set_layer(self.win, ls.Layer.OVERLAY)
        ls.set_keyboard_mode(self.win, ls.KeyboardMode.ON_DEMAND)
        for edge in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT, ls.Edge.RIGHT):
            ls.set_anchor(self.win, edge, True)

        self.canvas = Gtk.Fixed()
        # A Fixed gives a child its natural size and nothing more, so a
        # child that does not ask for one gets none -- and a widget with
        # no area receives no pointer events, however well it draws. The
        # overlay was told how big the surface is; say so.
        self.child.set_size_request(self.w, self.h)
        self.canvas.put(self.child, 0, 0)
        self.win.set_child(self.canvas)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda _c, x, y: self.place_at(x, y))
        motion.connect("motion", lambda _c, x, y: self.place_at(x, y))
        self.win.add_controller(motion)
        # KWin does not send pointer-enter until the mouse moves, so ask
        # the surface where the pointer is. wlroots answers only after a
        # pointer event, which cannot happen before the first frame is up,
        # so the give-up countdown waits for paint.
        self._armed = True
        self.win.add_tick_callback(self._painted)
        GLib.timeout_add(50, self._poll_pointer)
        if self.hit_widgets is not None:
            GLib.timeout_add(250, self.update_input_region)
        return True

    def enable_drag(self, handle_test):
        """Let a press that *handle_test(x, y)* accepts move the child."""
        self._drag_test = handle_test
        drag = Gtk.GestureDrag()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.canvas.add_controller(drag)

    # --- placement -----------------------------------------------------
    def _painted(self, _w, _clock):
        self._ticks += 1
        return self._ticks < 2

    def _poll_pointer(self):
        if self.placed or self.canvas is None:
            self._armed = False
            return False
        surface = self.win.get_surface()
        if surface is not None and self.win.get_width() > 0:
            seat = Gdk.Display.get_default().get_default_seat()
            ok, x, y, _mask = surface.get_device_position(seat.get_pointer())
            if ok and (x or y):
                self.place_at(x, y)
                self._armed = False
                return False
            if self._ticks >= 2:
                self._polls += 1
        if self._polls > 40 and self.win.get_width() > 0:
            # Pointer is on another output, or the query is unsupported.
            self.place_at(self.win.get_width() / 2,
                          self.win.get_height() / 2)
            self._armed = False
            return False
        return True

    def place_at(self, x, y, anchor=None):
        """Put the child where the pointer is, once, clamped on screen.

        *anchor* is the child point that lands under the pointer, as a
        fraction of its size: (0.5, 0.5) centres it, which is what a
        ring wants; a card that grows upward anchors near its bottom.
        Defaults to the anchor the overlay was built with, because the
        pointer handlers that call this cannot know the child's shape.
        """
        if self.placed or self.canvas is None:
            return
        self.placed = True
        ax, ay = anchor or self.anchor
        sw = self.win.get_width() or self.w
        sh = self.win.get_height() or self.h
        c = self.clamp
        px = min(max(x - self.w * ax, c), max(sw - self.w - c, c))
        py = min(max(y - self.h * ay, c), max(sh - self.h - c, c))
        self.move(px, py)
        if self.on_place is not None:
            self.on_place(px, py)

    def rearm(self):
        """Forget where the child was put, so the next showing finds the
        pointer again.

        A surface that is its own process places itself once and dies
        with the window. A resident one is shown and hidden over and
        over, and `placed` outlived the hiding: the menu came back
        wherever it had first appeared, however far the pointer had
        moved since, which reads as a surface that ignores the pointer
        or remembers a stale position.
        """
        self.placed = False
        self._polls = 0
        self._ticks = 0
        if self.canvas is None:
            return
        # Ask once, here and now, in case the surface can already
        # answer: then the child is placed before it is ever drawn
        # again, and nothing is seen at the old position.
        if not self._poll_pointer():
            return
        # It could not. A hidden window reports no size, so the pointer
        # cannot be found until it is back on screen and painted. Park
        # the child out of view for those few frames rather than start
        # the showing at the last one's position and jump.
        self._place_children(-self.w - 1, -self.h - 1)
        if self._armed:
            return
        self._armed = True
        self.win.add_tick_callback(self._painted)
        GLib.timeout_add(50, self._poll_pointer)

    def move(self, x, y):
        self.pos = (x, y)
        self._place_children(x, y)

    def _place_children(self, x, y):
        self.canvas.move(self.child, x, y)
        for widget, (dx, dy) in self._floating:
            self.canvas.move(widget, x + dx, y + dy)

    def add_floating(self, widget, offset):
        """Put *widget* on the canvas at a fixed offset from the child.

        For the ring that hangs off a card: opening a submenu makes it
        wider and taller than the card it belongs to, and a child inside
        the card's own box would either be clipped or push the
        conversation around every time someone opened a menu.
        """
        self._floating.append((widget, offset))
        self.canvas.put(widget, self.pos[0] + offset[0],
                        self.pos[1] + offset[1])

    # --- dragging ------------------------------------------------------
    def _drag_begin(self, _g, x, y):
        self._drag_from = self.pos if self._drag_test(x, y) else None

    def _drag_update(self, gesture, dx, dy):
        if self._drag_from is None:
            return
        if abs(dx) > self.DRAG_SLOP or abs(dy) > self.DRAG_SLOP:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._place_children(self._drag_from[0] + dx, self._drag_from[1] + dy)

    def _drag_end(self, _g, dx, dy):
        if self._drag_from is not None:
            self.pos = (self._drag_from[0] + dx, self._drag_from[1] + dy)
            self._drag_from = None

    # --- click-through --------------------------------------------------
    def freeze_input(self):
        """Take no clicks at all, for a surface that is handing over.

        It is still a fullscreen overlay while it waits for the surface
        arriving on top of it, and two of them both claiming input is
        one too many.
        """
        self._frozen = True
        clip_input_region(self.win, ())

    def update_input_region(self):
        if self._frozen:
            return True
        return clip_input_region(self.win, self.hit_widgets())


class ProgressBubble(Gtk.Overlay):
    """A bubble wearing a progress arc: blue ring fills 0..1 clockwise
    from the top, or spins when indeterminate. For onboarding and model
    downloads."""

    ARC_PAD = ARC_PAD
    ARC_W = ARC_W

    def __init__(self, icon="folder-download-symbolic",
                 diameter=STYLE_MENU.bubble):
        super().__init__()
        self._fraction = 0.0
        self._spinning = False
        self._d = diameter
        side = diameter + 2 * self.ARC_PAD
        self.set_size_request(side, side)

        # inner is public: callers restyle it (the setup wizard brightens
        # its emblem, which sits on a dark page instead of a wallpaper).
        self.inner = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.inner.add_css_class("bubble")
        self.inner.set_size_request(diameter, diameter)
        self._icon = Gtk.Image.new_from_icon_name(icon)
        self._icon.set_hexpand(True)
        self.inner.append(self._icon)
        self.set_child(self.inner)

        self._arc = Gtk.DrawingArea()
        self._arc.set_can_target(False)
        self._arc.set_draw_func(self._draw)
        self.add_overlay(self._arc)

    def set_icon(self, icon):
        self._icon.set_from_icon_name(icon)

    def set_icon_size(self, px):
        self._icon.set_pixel_size(px)

    def set_fraction(self, fraction):
        self._fraction = _clamp(fraction)
        self._spinning = False
        self._arc.queue_draw()

    def set_indeterminate(self, spinning=True):
        if spinning and not self._spinning:
            self._spinning = True
            self._arc.add_tick_callback(self._spin_tick)
        elif not spinning:
            self._spinning = False
            self._arc.queue_draw()   # the spin tick is gone; repaint once

    def _spin_tick(self, _w, _clock):
        self._arc.queue_draw()
        return self._spinning

    def _draw(self, area, cr, w, h):
        cx, cy = w / 2, h / 2
        r = self._d / 2 + self.ARC_PAD - self.ARC_W / 2
        cr.set_line_width(self.ARC_W)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        # faint full track
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(0x8a / 255, 0xb4 / 255, 0xf8 / 255, 1.0)
        if self._spinning:
            phase = (GLib.get_monotonic_time() / 1e6) * 1.6 * math.pi
            cr.arc(cx, cy, r, phase, phase + 0.55 * math.pi)
            cr.stroke()
        elif self._fraction > 0:
            top = -math.pi / 2
            cr.arc(cx, cy, r, top, top + 2 * math.pi * self._fraction)
            cr.stroke()


def _progress_demo():
    """RADIAL_DEMO=progress: a ProgressBubble cycling on a dark square,
    for capture on the demo stage."""

    def activate(app):
        win = Gtk.ApplicationWindow(application=app, decorated=False,
                                    default_width=180, default_height=120)
        apply_css()
        box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                      spacing=24)
        det = ProgressBubble()
        det.set_fraction(0.0)
        spin = ProgressBubble(icon="emblem-synchronizing-symbolic")
        spin.set_indeterminate(True)
        box.append(det)
        box.append(spin)
        win.set_child(box)

        state = {"f": 0.0}

        def step():
            state["f"] += 0.01
            if state["f"] > 1.0:
                state["f"] = 0.0
            det.set_fraction(state["f"])
            return True

        GLib.timeout_add(50, step)
        win.present()

    app = Gtk.Application(application_id="io.github.rebreda.dictatr.radial")
    app.connect("activate", activate)
    app.run([])


def _layout_demo():
    """A ring you can push around, for judging what no test can.

    Assertions can say the bubbles do not touch and that the arc misses
    the card. Whether the result looks right is a different question,
    and this is the answer to it: change the item count, the grouping,
    the arc and the depth by hand and watch what the layout does.
    """
    HELP = ("+/-  items   g  groups   o  obstacle   [ ]  move it   "
            "s  style   Return  deeper   Backspace  back")

    def activate(app):
        state = {"n": 6, "grouped": False, "style": STYLE_MENU,
                 "obstacle": False, "oy": 40}
        win = Gtk.ApplicationWindow(application=app, title="radial layout")
        win.set_default_size(760, 760)
        canvas = Gtk.Fixed()
        block = Gtk.Box()
        block.set_size_request(420, 260)
        block.add_css_class("msg")
        holder = {}

        def items(n, grouped, tag="a"):
            return [Bubble("dialog-information-symbolic", f"{tag}{i + 1}",
                           lambda: None,
                           group=(f"g{i // 3}" if grouped else ""))
                    for i in range(n)]

        status = Gtk.Label()
        status.add_css_class("status-pill")

        def rebuild():
            if "ring" in holder:
                canvas.remove(holder["ring"])
            ring = Ring(items(state["n"], state["grouped"]),
                        style=state["style"],
                        obstacles=(lambda: (block,)) if state["obstacle"]
                        else None)
            canvas.put(ring, 380 - ring.size / 2, 470 - ring.size / 2)
            holder["ring"] = ring
            ring.open()
            refresh()

        def refresh():
            ring = holder["ring"]
            m = ring.levels[-1].metrics
            status.set_label(
                f"{len(ring.levels[-1].visible())} items  r={m.radius:.0f}  "
                f"bubble={m.bubble:.0f}  depth={ring.depth}  "
                f"{'grouped' if state['grouped'] else 'one group'}  "
                f"{'obstacle' if state['obstacle'] else 'free circle'}")

        def on_key(_c, keyval, _code, _mod):
            ring = holder["ring"]
            ch = chr(keyval) if 32 <= keyval < 127 else ""
            if ch in "+=":
                state["n"] = min(state["n"] + 1, 24)
            elif ch in "-_":
                state["n"] = max(state["n"] - 1, 1)
            elif ch == "g":
                state["grouped"] = not state["grouped"]
            elif ch == "o":
                state["obstacle"] = not state["obstacle"]
                block.set_visible(state["obstacle"])
            elif ch == "[":
                state["oy"] = max(state["oy"] - 30, -240)
            elif ch == "]":
                state["oy"] = min(state["oy"] + 30, 320)
            elif ch == "s":
                state["style"] = (STYLE_CARD if state["style"] is STYLE_MENU
                                  else STYLE_MENU)
            elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                ring.push(items(state["n"], state["grouped"],
                                f"d{ring.depth + 1}."))
                GLib.timeout_add(320, lambda: (refresh(), False)[1])
                return True
            elif keyval == Gdk.KEY_BackSpace:
                ring.back()
                GLib.timeout_add(320, lambda: (refresh(), False)[1])
                return True
            else:
                return False
            canvas.move(block, 170, state["oy"])
            rebuild()
            return True

        canvas.put(block, 170, state["oy"])
        block.set_visible(False)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        column.append(canvas)
        row = Gtk.Box(halign=Gtk.Align.CENTER, spacing=10)
        row.append(status)
        column.append(row)
        hint = Gtk.Label(label=HELP)
        hint.add_css_class("status-pill")
        column.append(hint)
        win.set_child(column)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", on_key)
        win.add_controller(keys)
        rebuild()
        win.present()

    app = Gtk.Application(application_id="io.github.rebreda.dictatr.layout")
    app.connect("activate", lambda a: (apply_css(), activate(a)))
    app.run([])


def _tether_demo():
    """The line that joins two surfaces, on a keypress.

    A handoff is three hundred milliseconds long and happens between two
    processes, which is a hard thing to look at. Here it is between two
    hubs in one window: space attaches, space again releases.
    """
    def activate(app):
        win = Gtk.ApplicationWindow(application=app, title="radial tether")
        win.set_default_size(620, 420)
        canvas = Gtk.Fixed()
        origin, target = (110.0, 110.0), (470.0, 300.0)
        for (x, y), size in ((origin, STYLE_MENU.hub), (target, STYLE_CARD.hub)):
            hub = Gtk.Button(icon_name="audio-input-microphone-symbolic")
            hub.add_css_class("hub")
            hub.set_size_request(round(size), round(size))
            canvas.put(hub, x - size / 2, y - size / 2)
        tether = Tether(origin, target)
        tether.set_size_request(620, 420)
        canvas.put(tether, 0, 0)

        status = Gtk.Label(label="space: attach")
        status.add_css_class("status-pill")
        state = {"on": False}

        def on_key(_c, keyval, _code, _mod):
            if keyval != Gdk.KEY_space:
                return False
            state["on"] = not state["on"]
            if state["on"]:
                status.set_label("attaching…")
                tether.attach(lambda: status.set_label("taut — space to release"))
            else:
                status.set_label("releasing…")
                tether.detach(lambda: status.set_label("gone — space to attach"))
            return True

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        column.append(canvas)
        row = Gtk.Box(halign=Gtk.Align.CENTER)
        row.append(status)
        column.append(row)
        win.set_child(column)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", on_key)
        win.add_controller(keys)
        win.present()

    app = Gtk.Application(application_id="io.github.rebreda.dictatr.tether")
    app.connect("activate", lambda a: (apply_css(), activate(a)))
    app.run([])


if __name__ == "__main__":
    demo = os.environ.get("RADIAL_DEMO")
    if demo == "progress":
        _progress_demo()
    elif demo == "layout":
        _layout_demo()
    elif demo == "tether":
        _tether_demo()
    else:
        print("radial.py is a library. RADIAL_DEMO=layout is a playground "
              "for the ring's geometry, RADIAL_DEMO=tether shows the line "
              "that joins two surfaces, RADIAL_DEMO=progress shows the "
              "progress bubble.")
