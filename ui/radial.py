"""Radial UI kit for dictatr — round bubbles twirling around a hub.

The menu's visual identity, extracted so every surface (menu, onboarding,
downloads) draws from one vocabulary: dark translucent bubbles with thin
white borders, blue hub, green "on" accent, red danger accent.

Pieces:
  Bubble          a spec: icon, tooltip, action, css classes, children
  Ring            hub + satellites on an orbit, twirl-in/out, submenus
  ProgressBubble  a bubble wearing a progress arc (determinate or spinning)

Submenus: a Bubble with children, when activated, pulls the ring back
into the hub while the chosen bubble glides to the center and becomes
the hub; its children then twirl out. The hub in a submenu is a Back
control that reverses the animation. Escape backs out one level.

`RADIAL_DEMO=progress python3 ui/radial.py` shows a ProgressBubble
cycling through determinate and indeterminate modes, for stage capture.
"""

import math
import os
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


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
SIZE = 250          # circle bounding box
BUBBLE = 52         # satellite diameter
CENTER_BUBBLE = 58  # hub diameter
RADIUS = 84         # orbit radius

# --- animation ---------------------------------------------------------
ANIM_S = 0.45       # opening twirl duration
STAGGER_S = 0.05    # per-bubble delay while opening
TWIRL_RAD = 2.2     # extra rotation that unwinds during the twirl
OUT_S = 0.30        # dismissal twirl-out duration
SUB_OUT_S = 0.18    # ring collapse half of a submenu hop
SUB_IN_S = 0.26     # ring bloom half of a submenu hop
SUB_STAGGER_S = 0.04

# progress arc geometry, shared by Ring's hub and ProgressBubble
# A submenu keeps its parent ring on screen, pushed out and dimmed, so
# the level you came from stays a click away instead of a memory.
PARENT_RADIUS_K = 1.72   # times RADIUS
PARENT_ALPHA = 0.34

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


def _ease_out(p):
    return 1 - (1 - p) ** 3


def _clamp(p):
    return min(max(p, 0.0), 1.0)


class Bubble:
    """One satellite: icon + tooltip + either an action or children.

    css lists extra style classes ("on" paints it green). A bubble with
    children opens a submenu instead of firing an action.
    """

    def __init__(self, icon, tooltip, action=None, css=(), children=None):
        self.icon = icon
        self.tooltip = tooltip
        self.action = action
        self.css = tuple(css)
        self.children = list(children) if children else None


class Ring(Gtk.Fixed):
    """The radial circle: a hub with satellites twirling on an orbit.

    A SIZE x SIZE Gtk.Fixed — position it like any widget (menu.py drops
    it on a fullscreen canvas at the pointer). Call open() once placed;
    dismiss(then=...) spirals everything back in and then calls through.
    handle_key() consumes Escape (back one level; False at root so the
    caller can close) and number keys for the visible ring.
    """

    def __init__(self, items, hub_icon="window-close-symbolic",
                 hub_tooltip="Close  [Esc]", on_root_hub=None):
        super().__init__()
        self.set_size_request(SIZE, SIZE)
        self._cx = self._cy = SIZE // 2
        self.on_root_hub = on_root_hub
        self._root_hub = (hub_icon, hub_tooltip)
        self._state = "closed"   # closed|opening|open|busy|dismissing
        self._gen = 0            # bumping this cancels the running tick
        self._stack = []         # parent levels: (sats, hub_icon, hub_tip)
        self._parent_handlers = {}   # dimmed parents -> click handler id

        self._sats = self._make_sats(items)   # satellites before the hub
        hub = Gtk.Button(icon_name=hub_icon, tooltip_text=hub_tooltip)
        hub.add_css_class("hub")
        hub.set_size_request(CENTER_BUBBLE, CENTER_BUBBLE)
        hub.set_opacity(0.0)
        hub.set_focusable(False)   # same reason as the satellites
        hub.connect("clicked", self._on_hub)
        self.put(hub, self._cx - CENTER_BUBBLE / 2,
                 self._cy - CENTER_BUBBLE / 2)
        self.hub = hub

        # A progress arc around the hub, for rings that front a long
        # operation (a model download, a probe). Drawn in the same Fixed
        # so it tracks the hub, and never a target so clicks pass through.
        self._fraction = 0.0
        self._spinning = False
        side = CENTER_BUBBLE + 2 * ARC_PAD
        self._arc = Gtk.DrawingArea()
        self._arc.set_size_request(side, side)
        self._arc.set_can_target(False)
        self._arc.set_draw_func(self._draw_arc)
        self.put(self._arc, self._cx - side / 2, self._cy - side / 2)

    # --- hub progress ---------------------------------------------------
    def set_fraction(self, fraction):
        self._fraction = _clamp(fraction)
        self._spinning = False
        self._arc.queue_draw()

    def set_indeterminate(self, spinning=True):
        if spinning and not self._spinning:
            self._spinning = True
            self._arc.add_tick_callback(
                lambda *_: (self._arc.queue_draw(), self._spinning)[1])
        elif not spinning:
            self._spinning = False
            self._arc.queue_draw()

    def _draw_arc(self, _area, cr, w, h):
        if not self._spinning and self._fraction <= 0:
            return
        cx, cy = w / 2, h / 2
        r = CENTER_BUBBLE / 2 + ARC_PAD - ARC_W / 2
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

    # --- construction --------------------------------------------------
    def _make_sats(self, items, below_hub=False):
        sats = []
        for i, bubble in enumerate(items):
            b = Gtk.Button(icon_name=bubble.icon,
                           tooltip_text=f"{bubble.tooltip}  [{i + 1}]")
            b.add_css_class("bubble")
            for c in bubble.css:
                b.add_css_class(c)
            b.set_size_request(BUBBLE, BUBBLE)
            b.set_opacity(0.0)
            # Not focusable on purpose: a focused button activates on
            # space or Return, so a ring that maps under a stray keypress
            # would fire an action nobody chose. The ring is driven by
            # the pointer and by handle_key's number keys.
            b.set_focusable(False)
            b.connect("clicked", lambda _b, idx=i: self.activate(idx))
            angle = -math.pi / 2 + i * (2 * math.pi / len(items))
            self.put(b, self._cx - BUBBLE / 2, self._cy - BUBBLE / 2)
            if below_hub:
                b.insert_before(self, self.hub)
            sats.append((b, angle, bubble))
        return sats

    @property
    def depth(self):
        return len(self._stack)

    # --- animation driver ----------------------------------------------
    def _drive(self, duration, update, done=None):
        self._gen += 1
        gen = self._gen
        t0 = [None]

        def tick(_w, clock):
            if gen != self._gen:
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

        self.add_tick_callback(tick)

    def _twirl_in(self, sats, duration, stagger, done=None, hub_fade=False):
        span = duration - (len(sats) - 1) * stagger

        def update(t):
            if hub_fade:
                self.hub.set_opacity(min(1.0, t / 0.15))
            for i, (b, angle, _) in enumerate(sats):
                e = _ease_out(_clamp((t - i * stagger) / span))
                a = angle + (1 - e) * TWIRL_RAD
                r = RADIUS * e
                self.move(b, self._cx + r * math.cos(a) - BUBBLE / 2,
                          self._cy + r * math.sin(a) - BUBBLE / 2)
                b.set_opacity(e)

        self._drive(duration, update, done)

    def _twirl_out(self, sats, duration, stagger, done=None, hub_fade=False,
                   skip=None):
        span = duration - (len(sats) - 1) * stagger

        def update(t):
            if hub_fade:
                self.hub.set_opacity(max(0.0, 1.0 - t / duration))
            for i, (b, angle, _) in enumerate(sats):
                if b is skip:
                    continue
                e = 1 - _ease_out(_clamp((t - i * stagger) / span))
                a = angle + (1 - e) * TWIRL_RAD
                r = RADIUS * e
                self.move(b, self._cx + r * math.cos(a) - BUBBLE / 2,
                          self._cy + r * math.sin(a) - BUBBLE / 2)
                b.set_opacity(e)

        self._drive(duration, update, done)

    def _snap_open(self):
        """Jump the current ring to its settled pose (cancels any tick)."""
        self._gen += 1
        self.hub.set_opacity(1.0)
        for b, angle, _ in self._sats:
            self.move(b, self._cx + RADIUS * math.cos(angle) - BUBBLE / 2,
                      self._cy + RADIUS * math.sin(angle) - BUBBLE / 2)
            b.set_opacity(1.0)

    # --- lifecycle ------------------------------------------------------
    def open(self):
        """Twirl the root ring out of the hub."""
        self._state = "opening"

        def settled():
            if self._state == "opening":
                self._state = "open"

        self._twirl_in(self._sats, ANIM_S, STAGGER_S, settled, hub_fade=True)

    def dismiss(self, then=None):
        """Spiral everything back into the hub, fading, then call through."""
        if self._state == "dismissing":
            return
        self._state = "dismissing"
        self._twirl_out(self._sats, OUT_S, 0.02, then, hub_fade=True)

    # --- activation & submenus -----------------------------------------
    def activate(self, index):
        if self._state not in ("open", "opening") or \
                index >= len(self._sats):
            return
        _b, _a, bubble = self._sats[index]
        if bubble.children:
            if self._state == "opening":
                self._snap_open()
                self._state = "open"
            self._push(index)
        elif bubble.action is not None:
            bubble.action()

    def _push(self, index):
        self._state = "busy"
        chosen_btn, chosen_angle, chosen = self._sats[index]
        parent_sats = self._sats
        # The chosen bubble rides above the hub while it glides in.
        chosen_btn.insert_after(self, self.hub)

        others = [s for s in parent_sats if s[0] is not chosen_btn]

        outer = RADIUS * PARENT_RADIUS_K

        def phase_a(t):
            p = _ease_out(_clamp(t / SUB_OUT_S))
            for b, angle, _ in others:
                r = RADIUS + (outer - RADIUS) * p
                self.move(b, self._cx + r * math.cos(angle) - BUBBLE / 2,
                          self._cy + r * math.sin(angle) - BUBBLE / 2)
                b.set_opacity(1.0 - (1.0 - PARENT_ALPHA) * p)
            r = RADIUS * (1 - p)   # the chosen bubble glides straight in
            self.move(chosen_btn,
                      self._cx + r * math.cos(chosen_angle) - BUBBLE / 2,
                      self._cy + r * math.sin(chosen_angle) - BUBBLE / 2)

        def phase_b():
            chosen_btn.set_opacity(0.0)
            chosen_btn.insert_before(self, self.hub)
            # The parents that stayed are now a way back: clicking one
            # returns to their level and opens it, so a wrong turn costs
            # one click instead of a trip through the hub.
            for i, (b, _a, item) in enumerate(parent_sats):
                if b is chosen_btn:
                    continue
                b.add_css_class("parent")
                b.set_tooltip_text(f"Back to {item.tooltip}")
                if b in self._parent_handlers:
                    b.disconnect(self._parent_handlers.pop(b))
                self._parent_handlers[b] = b.connect(
                    "clicked", lambda _b, idx=i: self.back(then=idx))
            self._stack.append((parent_sats, self.hub.get_icon_name(),
                                self.hub.get_tooltip_text()))
            self.hub.set_icon_name("go-previous-symbolic")
            self.hub.set_tooltip_text(f"Back to {chosen.tooltip}  [Esc]")
            self.hub.add_css_class("back")
            self._sats = self._make_sats(chosen.children, below_hub=True)

            def settled():
                self._state = "open"

            self._twirl_in(self._sats, SUB_IN_S, SUB_STAGGER_S, settled)

        self._drive(SUB_OUT_S, phase_a, phase_b)

    def set_hub(self, icon=None, tooltip=None):
        """Relabel the hub in place, for a surface whose state changes
        without the ring changing (thinking, then working, then done)."""
        if icon:
            self.hub.set_icon_name(icon)
        if tooltip is not None:
            self.hub.set_tooltip_text(tooltip)

    def swap(self, items, hub_icon=None, hub_tooltip=None, forward=True,
             done=None):
        """Replace the visible satellites with a new set, in the submenu
        vocabulary: the old ring spirals into the hub, the hub becomes
        the new level's icon, the new ring blooms. The wizard walks its
        steps with this; unlike a submenu it keeps no stack, because a
        step is a sibling of the last, not a child."""
        if self._state not in ("open", "opening"):
            # Mid-animation: a dropped swap would strand the caller's
            # done callback, and with it whatever it was going to draw.
            # Land the current one first, then run this.
            GLib.timeout_add(
                60, lambda: (self.swap(items, hub_icon, hub_tooltip,
                                       forward, done), False)[1])
            return
        if self._state == "opening":
            self._snap_open()
        self._state = "busy"
        old = self._sats

        def bloom():
            for b, _, _ in old:
                self.remove(b)
            if hub_icon:
                self.hub.set_icon_name(hub_icon)
            if hub_tooltip:
                self.hub.set_tooltip_text(hub_tooltip)
            self._sats = self._make_sats(items, below_hub=True)

            def settled():
                self._state = "open"
                if done is not None:
                    done()

            self._twirl_in(self._sats, SUB_IN_S, SUB_STAGGER_S, settled)

        self._twirl_out(old, SUB_OUT_S, 0.0 if forward else SUB_STAGGER_S,
                        bloom)

    def back(self, then=None):
        """Collapse the submenu and bring the parent ring back in.

        *then* is a parent index to activate once the ring has settled,
        which is what clicking a dimmed parent does: leave here and open
        that instead, without a stop at the level in between."""
        if self._state != "open" or not self._stack:
            return
        self._state = "busy"
        children = self._sats
        parents = self._stack[-1][0]
        chosen_btn = self.hub   # the parent that became the hub, if any

        def phase_b():
            for b, _, _ in children:
                self.remove(b)
            sats, icon, tip = self._stack.pop()
            self.hub.set_icon_name(icon)
            self.hub.set_tooltip_text(tip)
            if not self._stack:
                self.hub.remove_css_class("back")
            self._sats = sats
            for i, (b, _a, item) in enumerate(sats):
                b.remove_css_class("parent")
                b.set_tooltip_text(f"{item.tooltip}  [{i + 1}]")
                if b in self._parent_handlers:
                    b.disconnect(self._parent_handlers.pop(b))

            def settled():
                self._state = "open"
                if then is not None:
                    self.activate(then)

            self._twirl_in(self._sats, SUB_IN_S, SUB_STAGGER_S, settled)

        # The parents that stayed visible slide home; the ones that were
        # never there (the level being left) twirl out as before.
        staying = [s for s in parents if s[0] is not chosen_btn]
        outer = RADIUS * PARENT_RADIUS_K

        def phase_a(t):
            p = _ease_out(_clamp(t / SUB_OUT_S))
            for b, angle, _ in staying:
                r = outer + (RADIUS - outer) * p
                self.move(b, self._cx + r * math.cos(angle) - BUBBLE / 2,
                          self._cy + r * math.sin(angle) - BUBBLE / 2)
                b.set_opacity(PARENT_ALPHA + (1.0 - PARENT_ALPHA) * p)
            e = 1 - p
            for b, angle, _ in children:
                a = angle + (1 - e) * TWIRL_RAD
                r = RADIUS * e
                self.move(b, self._cx + r * math.cos(a) - BUBBLE / 2,
                          self._cy + r * math.sin(a) - BUBBLE / 2)
                b.set_opacity(e)

        self._drive(SUB_OUT_S, phase_a, phase_b)

    def _on_hub(self, _btn):
        if self._stack:
            self.back()
        elif self.on_root_hub is not None:
            self.on_root_hub()

    # --- keyboard -------------------------------------------------------
    def handle_key(self, keyval):
        """Escape backs out one level (False at root: caller closes);
        number keys activate the visible ring."""
        if keyval == Gdk.KEY_Escape:
            if self._stack and self._state == "open":
                self.back()
                return True
            return False
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_0 + len(self._sats):
            self.activate(keyval - Gdk.KEY_1)
            return True
        return False


class ProgressBubble(Gtk.Overlay):
    """A bubble wearing a progress arc: blue ring fills 0..1 clockwise
    from the top, or spins when indeterminate. For onboarding and model
    downloads."""

    ARC_PAD = ARC_PAD
    ARC_W = ARC_W

    def __init__(self, icon="folder-download-symbolic", diameter=BUBBLE):
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


if __name__ == "__main__":
    if os.environ.get("RADIAL_DEMO") == "progress":
        _progress_demo()
    else:
        print("radial.py is a library; RADIAL_DEMO=progress shows the "
              "progress bubble demo")
