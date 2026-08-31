#!/usr/bin/python3
"""One widget that draws the whole scene, and the zoom that moves through it.

Every surface used to be a tree of GtkButtons, which meant a bubble could
only be moved, scaled and faded — never turned into something else. Here
the scene is drawn: one widget, GSK render nodes, and a camera. That buys
three things the widget tree could not give.

A bubble can *become* a card, because both are outlines and an outline can
be interpolated into another one. Nothing has to fade out while its
replacement fades in.

Depth is a zoom rather than a swap. Every level is drawn in its own space
and the camera sits at a fractional depth between them (see ui/graph.py),
so going deeper is one continuous movement and looks the same at depth 1
and depth 20. The camera's depth is a spring, so a traversal can be caught
half way and sent back without the movement ever restarting.

And it can be looked at without a screen: the same snapshot that GTK
draws renders to a PNG through Gsk.CairoRenderer, so `--png` is a picture
of exactly what a display would show, and no compositor is involved.

The cost is that bubbles are no longer widgets, so hit-testing, hover,
press and accessibility are ours. Hit-testing is the camera transform
inverted, which is cheaper than walking a widget tree; the rest is below.
"""

import math
import os
import sys
from pathlib import Path as _Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import (Gdk, GLib, Gsk, Gtk,  # noqa: E402
                           Graphene, Pango)

sys.path.insert(0, str(_Path(__file__).resolve().parent))
import graph as G  # noqa: E402
import motion  # noqa: E402
import radial_layout as L  # noqa: E402

# The palette, as GSK wants it. radial.py holds the same values as CSS;
# a drawn scene cannot ask a stylesheet what colour to be.
INK = Gdk.RGBA()
INK.parse("#e8eaf1")
CHARCOAL = Gdk.RGBA()
CHARCOAL.parse("#1c1d22")
BLUE = Gdk.RGBA()
BLUE.parse("#8ab4f8")
GREEN = Gdk.RGBA()
GREEN.parse("#81c995")
RED = Gdk.RGBA()
RED.parse("#f28b82")
EDGE = Gdk.RGBA()
EDGE.parse("#ffffff")


def _trace(msg):
    """One line per pointer event, when the shell debug topic is on.

    The canvas does its own hit-testing, so when a bubble will not press
    there is nothing to inspect: no widget to find, no handler to breakpoint.
    This says whether the event arrived at all, where it landed, and what
    was under it -- which is the difference between an input region that
    never delivered the click and arithmetic that missed the bubble.
    """
    topics = os.environ.get("DICTATE_DEBUG", "")
    if "shell" in topics or "all" in topics:
        print(f"canvas: {msg}", file=sys.stderr, flush=True)


def rgba(base, alpha):
    out = Gdk.RGBA()
    out.red, out.green, out.blue = base.red, base.green, base.blue
    out.alpha = alpha
    return out


def circle(cx, cy, r):
    builder = Gsk.PathBuilder()
    builder.add_circle(Graphene.Point().init(cx, cy), r)
    return builder.to_path()


class Item:
    """One thing on a ring: what it looks like, and how it is feeling.

    `press` and `hover` are spring-driven values rather than CSS states,
    because a bubble that gives under the pointer and springs back is the
    difference between an interface that responds and one that merely
    highlights.
    """

    __slots__ = ("node", "hover", "press", "hover_v", "press_v")

    def __init__(self, node):
        self.node = node
        self.hover = self.press = 0.0
        self.hover_v = self.press_v = 0.0


class Canvas(Gtk.Widget):
    """The scene. Give it a graph and a root and it draws everything."""

    __gtype_name__ = "DictatrCanvas"

    def __init__(self, graph, root, style=L.STYLE_MENU, on_activate=None):
        super().__init__()
        self.graph = graph
        self.path = G.Path(graph, root)
        # What to do when a node with no children is chosen. Branches
        # zoom; leaves are the end of the road and belong to the surface.
        self.on_activate = on_activate
        self.style = style
        self.depth = 0.0            # fractional: the camera's position
        self.depth_v = 0.0          # ...and how fast it is moving
        self.spin = 0.0             # the ring's rotation, for drag
        self.spin_v = 0.0
        self.focus = None           # (level, index) under the pointer
        self._items = {}            # node id -> [Item]
        self._metrics = {}          # node id -> Metrics
        self.set_focusable(True)
        self.set_accessible_role(Gtk.AccessibleRole.MENU)

    # --- the levels ------------------------------------------------------
    def level_ids(self):
        return self.path.ids

    def items(self, node_id):
        if node_id not in self._items:
            self._items[node_id] = [Item(n) for n in self.graph.children(node_id)]
        return self._items[node_id]

    def metrics(self, node_id):
        if node_id not in self._metrics:
            kids = self.items(node_id)
            self._metrics[node_id] = L.solve(
                [i.node.group for i in kids], L.Arc.full(), self.style)
        return self._metrics[node_id]

    def extent(self, node_id):
        m = self.metrics(node_id)
        return m.radius + m.bubble / 2

    def camera(self):
        """A Camera built from the descents this path actually took."""
        steps = []
        for level, choice in enumerate(self.path.choices):
            parent = self.path.ids[level]
            m = self.metrics(parent)
            child = self.path.ids[level + 1]
            if choice >= len(m.angles):
                break
            angle = m.angles[choice]
            slot = (m.radius * math.cos(angle), m.radius * math.sin(angle))
            steps.append(G.descend(slot, m.bubble, self.extent(child)))
        return G.Camera(steps)

    def do_measure(self, orientation, _for_size):
        """How big the scene wants to be.

        A Gtk.Widget with no measure reports 0x0, and Overlay puts its
        child in a Gtk.Fixed, which gives a child exactly its natural
        size. The canvas therefore had a zero allocation: it still drew,
        because viewport() falls back to 800 when get_width() is 0, but
        GTK delivers no pointer event to a widget with no area -- so
        every bubble was unclickable while the keyboard went on working,
        the key controller being on the window rather than here.
        """
        side = self._forced_view or (self.SIDE, self.SIDE)
        want = side[0] if orientation == Gtk.Orientation.HORIZONTAL else side[1]
        return want, want, -1, -1

    SIDE = 900        # natural size when nothing has been forced

    def viewport(self):
        if self._forced_view is not None:
            return self._forced_view
        return (self.get_width() or 800, self.get_height() or 800)

    # --- drawing ---------------------------------------------------------
    def do_snapshot(self, snapshot):
        cam = self.camera()
        view = self.viewport()
        for level, scale, alpha in reversed(cam.visible(self.depth,
                                                        len(self.path.ids))):
            place = cam.place(level, self.depth, view)
            self._draw_level(snapshot, level, place, alpha)

    def _draw_level(self, snapshot, level, place, alpha):
        node_id = self.path.ids[level]
        m = self.metrics(node_id)
        kids = self.items(node_id)
        scale, x, y = place
        live = level == len(self.path.ids) - 1

        snapshot.push_opacity(alpha)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x, y))
        snapshot.scale(scale, scale)
        # Levels you are on your way past lose their edges first: it reads
        # as depth of field rather than as a fade.
        blurred = scale > 2.0
        if blurred:
            snapshot.push_blur(min(18.0, (scale - 2.0) * 2.5))

        self._hub(snapshot, self.graph[node_id], live)
        for i, (item, angle) in enumerate(zip(kids, m.angles)):
            turn = angle + (self.spin if live else 0.0)
            self._bubble(snapshot, item, m,
                         m.radius * math.cos(turn), m.radius * math.sin(turn),
                         live and self.focus == (level, i))
        if live:
            self._captions(snapshot, m, kids)
        if blurred:
            snapshot.pop()
        snapshot.restore()
        snapshot.pop()

    def _captions(self, snapshot, m, kids):
        """What the level is, under the hub; what you are pointing at,
        beside its bubble. Every name at once would be a wall of type."""
        node = self.path.node
        if node.title:
            self._text(snapshot, node.title, 0, self.style.hub / 2 + 13,
                       rgba(INK, 0.55), size=11.5)
        if self.focus is None or self.focus[0] != len(self.path.ids) - 1:
            return
        index = self.focus[1]
        if index >= len(m.angles) or index >= len(kids):
            return
        turn = m.angles[index] + self.spin
        away = m.radius + m.bubble / 2 + 15
        self._text(snapshot, kids[index].node.title,
                   away * math.cos(turn), away * math.sin(turn),
                   rgba(INK, 0.92), size=12.5)

    def _hub(self, snapshot, node, live):
        r = self.style.hub / 2
        snapshot.append_fill(circle(0, 0, r), Gsk.FillRule.WINDING,
                             rgba(CHARCOAL, 0.93))
        self._ring_stroke(snapshot, 0, 0, r, rgba(BLUE if live else EDGE,
                                                  0.55 if live else 0.10))
        if node.icon:
            self._icon(snapshot, node.icon, 0, 0,
                       self.style.hub * self.style.icon_ratio, BLUE)

    def _bubble(self, snapshot, item, m, cx, cy, focused):
        # Press pushes it in, hover lifts it: one radius, two springs.
        r = m.bubble / 2 * (1.0 + 0.09 * item.hover - 0.11 * item.press)
        fill = rgba(BLUE, 0.28) if item.hover > 0.01 else rgba(CHARCOAL, 0.93)
        snapshot.append_fill(circle(cx, cy, r), Gsk.FillRule.WINDING, fill)
        self._ring_stroke(snapshot, cx, cy, r,
                          rgba(BLUE, 0.60) if item.hover > 0.01
                          else rgba(EDGE, 0.10))
        if focused:
            self._ring_stroke(snapshot, cx, cy, r + 4, rgba(BLUE, 0.7), 1.5)
        if item.node.icon:
            self._icon(snapshot, item.node.icon, cx, cy,
                       m.bubble * self.style.icon_ratio, INK)

    def _ring_stroke(self, snapshot, cx, cy, r, colour, width=1.0):
        stroke = Gsk.Stroke.new(width)
        snapshot.append_stroke(circle(cx, cy, r), stroke, colour)

    def _icon(self, snapshot, name, cx, cy, size, colour):
        theme = Gtk.IconTheme.get_for_display(self.get_display()
                                              or Gdk.Display.get_default())
        # FORCE_SYMBOLIC or a theme that has no symbolic version of a name
        # hands back a full-colour icon, which snapshot_symbolic masks
        # into a solid disc. The kit already learned this once with
        # Breeze's input-keyboard-symbolic; a filled white circle where a
        # stop sign should be is the same bug.
        icon = theme.lookup_icon(name, None, max(int(size), 8), 1,
                                 Gtk.TextDirection.NONE,
                                 Gtk.IconLookupFlags.FORCE_SYMBOLIC)
        if icon is None:
            return
        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx - size / 2, cy - size / 2))
        if icon.is_symbolic():
            icon.snapshot_symbolic(snapshot, size, size, [colour])
        else:
            icon.snapshot(snapshot, size, size)
        snapshot.restore()

    def _text(self, snapshot, text, cx, cy, colour, size=12.0, width=190):
        """A line of type, centred on a point.

        Icon-only rings were tried in this codebase and reverted: the
        wizard's own docstring records that "the ring this replaced put
        every action behind an unlabelled icon and a hover". Drawn text
        costs nothing here, so what you are pointing at can say so.
        """
        layout = self.create_pango_layout(text)
        desc = layout.get_context().get_font_description()
        desc = desc.copy() if desc else Pango.FontDescription()
        desc.set_absolute_size(size * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_width(int(width * Pango.SCALE))
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        _ink, logical = layout.get_pixel_extents()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx - width / 2,
                                                 cy - logical.height / 2))
        snapshot.append_layout(layout, colour)
        snapshot.restore()

    # --- hit testing -----------------------------------------------------
    def hit(self, px, py):
        """(level, index) under a viewport point, or None.

        The camera transform inverted, then the ring's own geometry — no
        widget tree to search, and it works at any fractional depth.
        """
        cam = self.camera()
        view = self.viewport()
        for level, scale, alpha in cam.visible(self.depth, len(self.path.ids)):
            if alpha < 0.25:
                continue          # too faint to be aiming at
            node_id = self.path.ids[level]
            m = self.metrics(node_id)
            live = level == len(self.path.ids) - 1
            lx, ly = cam.from_viewport(level, self.depth, (px, py), view)
            for i, angle in enumerate(m.angles):
                turn = angle + (self.spin if live else 0.0)
                dx = lx - m.radius * math.cos(turn)
                dy = ly - m.radius * math.sin(turn)
                if dx * dx + dy * dy <= (m.bubble / 2) ** 2:
                    return level, i
        return None

    def hub_hit(self, px, py):
        cam = self.camera()
        view = self.viewport()
        level = len(self.path.ids) - 1
        lx, ly = cam.from_viewport(level, self.depth, (px, py), view)
        return math.hypot(lx, ly) <= self.style.hub / 2

    def slot_at(self, level, index):
        """Where a bubble is, in viewport pixels."""
        m = self.metrics(self.path.ids[level])
        if index >= len(m.angles):
            return None
        turn = m.angles[index] + (
            self.spin if level == len(self.path.ids) - 1 else 0.0)
        return self.camera().to_viewport(
            level, self.depth,
            (m.radius * math.cos(turn), m.radius * math.sin(turn)),
            self.viewport())

    # --- navigation ------------------------------------------------------
    def can_enter(self, index):
        node = self.path.node
        return (0 <= index < len(node.children)
                and bool(self.graph[node.children[index]].children))

    def enter(self, index):
        """Zoom into a child. The depth spring carries the movement, so
        the camera is already on its way when this returns."""
        if not self.path.enter(index):
            return False
        self.depth = max(self.depth, len(self.path.ids) - 2)
        self.announce()
        return True

    def activate(self, index):
        """Choose a leaf: the end of a road, and the surface's business."""
        node = self.path.node
        if not 0 <= index < len(node.children):
            return False
        chosen = self.graph[node.children[index]]
        if self.on_activate is not None:
            self.on_activate(chosen)
        return True

    def back(self):
        if not self.path.back():
            return False
        self.announce()
        return True

    def target_depth(self):
        return float(len(self.path.ids) - 1)

    def announce(self):
        """The accessibility story, such as it is.

        Bubbles stopped being widgets when the scene became drawn, so
        they stopped being focusable and screen-reader-visible for free.
        This puts the level and its choices on the canvas itself, which
        is a partial restoration and should be read as one — the number
        keys remain the honest keyboard route.
        """
        node = self.path.node
        names = ", ".join(f"{i + 1} {c.title}"
                          for i, c in enumerate(self.graph.children(node.id)))
        self.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [node.title or node.id, names])

    # --- continuous interaction ------------------------------------------
    # Everything below is a value on a spring rather than a state in a
    # stylesheet. A bubble does not switch to a hover colour, it swells;
    # it does not click, it gives; the ring does not jump to the next
    # level, it is pulled there and can be pulled back.

    def install_gestures(self):
        motion_c = Gtk.EventControllerMotion()
        motion_c.connect("motion", self._on_motion)
        motion_c.connect("leave", lambda *_: self._set_focus(None))
        self.add_controller(motion_c)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_press)
        click.connect("released", self._on_release)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def _set_focus(self, hit):
        if hit == self.focus:
            return
        self.focus = hit
        self.queue_draw()

    def _on_motion(self, _c, x, y):
        self._set_focus(self.hit(x, y))

    def _on_press(self, _g, _n, x, y):
        hit = self.hit(x, y)
        _trace(f"press at ({x:.0f},{y:.0f}) view={self.viewport()} "
               f"depth={self.depth:.2f} -> {hit}")
        self._set_focus(hit)
        self._pressed = hit
        self.queue_draw()

    def _on_release(self, _g, _n, x, y):
        pressed, self._pressed = self._pressed, None
        _trace(f"release at ({x:.0f},{y:.0f}) pressed={pressed} "
               f"dragged={self._dragged}")
        if pressed is None or self._dragged:
            return
        level, index = pressed
        if level != len(self.path.ids) - 1:
            self.go_to_level(level)          # a parent: back out to it
        elif self.can_enter(index):
            self.enter(index)
        else:
            self.activate(index)
        self.queue_draw()

    def go_to_level(self, level):
        while len(self.path.ids) - 1 > level:
            self.path.back()
        self.announce()

    # --- drag: turn the ring, and pull the next level in -----------------
    def _on_drag_begin(self, _g, x, y):
        self._drag_from = (x, y)
        self._drag_spin = self.spin
        self._drag_depth = self.depth
        self._dragged = False
        self._drag_into = self.hit(x, y)

    def _on_drag_update(self, _g, dx, dy):
        # Per axis, against the same slop radial.Overlay uses and the
        # desktop's own drag threshold. Summing the two axes at 6 meant a
        # 4px-by-3px wobble between press and release counted as a drag,
        # _on_release discarded the click, and no bubble could be pressed
        # with a hand rather than a keyboard.
        if abs(dx) > self.DRAG_SLOP or abs(dy) > self.DRAG_SLOP:
            self._dragged = True
        if self._drag_from is None:
            return
        into = self._drag_into
        live = len(self.path.ids) - 1
        if into is not None and into[0] == live and self.can_enter(into[1]):
            # Pulling toward a bubble draws its level in behind it. Let go
            # past half way and it completes; drag back and it returns.
            slot = self.slot_at(*into)
            if slot is not None:
                cx, cy = self.viewport()[0] / 2, self.viewport()[1] / 2
                reach = max(math.hypot(slot[0] - cx, slot[1] - cy), 1.0)
                along = (dx * (slot[0] - cx) + dy * (slot[1] - cy)) / reach
                self.depth = self._drag_depth + motion.clamp(along / reach)
                self.queue_draw()
                return
        # Otherwise the drag turns the ring, with the far side moving less.
        cx, cy = self.viewport()[0] / 2, self.viewport()[1] / 2
        x0, y0 = self._drag_from
        before = math.atan2(y0 - cy, x0 - cx)
        after = math.atan2(y0 + dy - cy, x0 + dx - cx)
        self.spin = self._drag_spin + (after - before)
        self.queue_draw()

    def _on_drag_end(self, gesture, dx, dy):
        into = self._drag_into
        self._drag_from = self._drag_into = None
        live = len(self.path.ids) - 1
        if into is not None and into[0] == live and self.can_enter(into[1]):
            if self.depth - self._drag_depth > 0.5:
                self.enter(into[1])          # committed
            else:
                self.depth = self._drag_depth
            self.queue_draw()
            return
        ok, vx, vy = gesture.get_velocity()
        if ok:
            cx, cy = self.viewport()[0] / 2, self.viewport()[1] / 2
            x0, y0 = gesture.get_start_point()[1:]
            reach = max(math.hypot(x0 - cx, y0 - cy), 1.0)
            # Tangential velocity, as radians per second.
            self.spin_v = (vx * -(y0 - cy) + vy * (x0 - cx)) / (reach * reach)
        self.queue_draw()

    def _on_scroll(self, _c, _dx, dy):
        """The wheel is the same axis as pulling a level in."""
        self.depth = motion.clamp(self.depth - dy * 0.18,
                                  0.0, float(len(self.path.ids) - 1))
        self.queue_draw()
        return True

    DRAG_SLOP = 8    # movement below this is still a click

    _pressed = None
    _dragged = False
    _drag_from = None
    _drag_into = None
    _drag_spin = 0.0
    _drag_depth = 0.0

    # --- the frame -------------------------------------------------------
    def tick(self, dt):
        """Advance every spring by *dt*. Returns True while anything moves.

        One place decides what is still in flight, so the surface can stop
        asking for frames when the scene has settled.
        """
        moving = False
        target = self.target_depth()
        if not motion.ZOOM.settled(self.depth, target, self.depth_v):
            self.depth, self.depth_v = motion.ZOOM.at(
                self.depth, target, self.depth_v, dt)
            moving = True
        if not motion.FLING.settled(self.spin, self.spin, self.spin_v):
            self.spin, self.spin_v = motion.FLING.at(
                self.spin, self.spin, self.spin_v, dt)
            moving = True
        for level, node_id in enumerate(self.path.ids):
            for i, item in enumerate(self.items(node_id)):
                want_hover = 1.0 if self.focus == (level, i) else 0.0
                want_press = 1.0 if self._pressed == (level, i) else 0.0
                for name, want, spring in (("hover", want_hover, motion.SNAPPY),
                                           ("press", want_press, motion.SNAPPY)):
                    value = getattr(item, name)
                    speed = getattr(item, name + "_v")
                    if spring.settled(value, want, speed):
                        continue
                    value, speed = spring.at(value, want, speed, dt)
                    setattr(item, name, value)
                    setattr(item, name + "_v", speed)
                    moving = True
        return moving

    # --- looking at it without a screen ----------------------------------
    def render_png(self, path, size=(900, 900)):
        """Draw the scene to a PNG through GSK, with no window involved.

        The same snapshot GTK would put on screen, so this is a picture of
        the real thing rather than a diagram of it.
        """
        self._forced_view = size
        snapshot = Gtk.Snapshot()
        self.do_snapshot(snapshot)
        node = snapshot.to_node()
        renderer = Gsk.CairoRenderer.new()
        renderer.realize(None)
        bounds = Graphene.Rect().init(0, 0, size[0], size[1])
        texture = renderer.render_texture(node, bounds)
        texture.save_to_png(str(path))
        renderer.unrealize()
        self._forced_view = None
        return path

    _forced_view = None

    # --- driving the frame ------------------------------------------------
    def animate(self):
        """Ask for frames while anything is still moving, and stop when
        nothing is. A scene at rest should cost nothing."""
        if self._ticking:
            return
        self._ticking = True
        last = [None]

        def frame(_w, clock):
            now = clock.get_frame_time() / 1e6
            dt = 0.0 if last[0] is None else min(now - last[0], 0.05)
            last[0] = now
            moving = self.tick(dt)
            self.queue_draw()
            if not moving:
                self._ticking = False
            return moving

        self.add_tick_callback(frame)

    _ticking = False


def demo():
    """`CANVAS_DEMO=1 python3 ui/canvas.py` — the scene, to play with.

    Hover, press, drag to turn the ring, drag toward a bubble to pull its
    level in and let go past half way to commit, scroll to zoom, click a
    parked ancestor to go back. The only way to judge whether any of this
    feels right is to move it about.
    """
    NODES = [
        G.Node("menu", "Menu", "view-more-symbolic",
               children=("dictate", "clip", "chat", "listen", "more",
                         "cancel")),
        G.Node("dictate", "Dictate", "audio-input-microphone-symbolic"),
        G.Node("clip", "To clipboard", "edit-copy-symbolic"),
        G.Node("chat", "Ask the AI", "dictatr-chat-symbolic",
               children=("chat-settings", "message")),
        G.Node("listen", "Always-on", "media-record-symbolic"),
        G.Node("more", "More", "view-more-symbolic",
               children=("file", "gc", "prefs", "setup")),
        G.Node("cancel", "Cancel", "process-stop-symbolic"),
        G.Node("chat-settings", "Chat settings", "emblem-system-symbolic",
               children=("details", "speak", "recall")),
        G.Node("message", "This message", "view-list-symbolic"),
        G.Node("file", "Transcribe a file", "folder-music-symbolic"),
        G.Node("gc", "Clean up", "user-trash-symbolic"),
        G.Node("prefs", "Settings", "preferences-system-symbolic"),
        G.Node("setup", "Set up", "dictatr-engine-symbolic"),
        G.Node("details", "Show the working", "view-list-symbolic"),
        G.Node("speak", "Speak answers", "audio-speakers-symbolic"),
        G.Node("recall", "Recall", "document-open-recent-symbolic"),
    ]

    def activate(app):
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        theme.add_search_path(str(_Path(__file__).resolve().parent
                                  / "icons" / "theme"))
        win = Gtk.ApplicationWindow(application=app, title="dictatr scene")
        win.set_default_size(900, 900)
        cv = Canvas(G.Graph(NODES), "menu")
        cv.install_gestures()
        cv.set_hexpand(True)
        cv.set_vexpand(True)
        win.set_child(cv)

        keys = Gtk.EventControllerKey()

        def on_key(_c, keyval, _code, _mod):
            if keyval == Gdk.KEY_Escape or keyval == Gdk.KEY_BackSpace:
                cv.back()
            elif Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
                i = keyval - Gdk.KEY_1
                cv.enter(i) if cv.can_enter(i) else cv.activate(i)
            else:
                return False
            cv.animate()
            return True

        keys.connect("key-pressed", on_key)
        win.add_controller(keys)
        # Anything that changes the scene asks for frames; the canvas
        # stops asking once everything has settled, so a scene at rest
        # is not burning a frame clock.
        GLib.timeout_add(40, lambda: (cv.animate(), True)[1])
        win.present()

    app = Gtk.Application(application_id="io.github.rebreda.dictatr.canvas")
    app.connect("activate", activate)
    app.run([])


if __name__ == "__main__":
    import os
    if os.environ.get("CANVAS_DEMO"):
        demo()
    else:
        print("canvas.py is a library. CANVAS_DEMO=1 python3 ui/canvas.py "
              "opens the scene to play with.")
