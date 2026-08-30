#!/usr/bin/env python3
"""Select, crop and mark up a screenshot. The portable half of Ctrl+Alt+G.

The Screenshot portal is the one capture path every Wayland desktop
answers, and all it does is hand back a picture. KDE has Spectacle
behind it, whose overlay crops and annotates in the same drag; other
desktops have nothing of the sort, and a screenshot on its way to a
question is rarely the whole screen with nothing marked on it.

So this is that overlay, in the toolkit dictatr already needs. It shows
the capture full-screen, dims everything outside the selection, and
draws on top of it. The result is the same on every desktop, which is
the point -- but Spectacle is still preferred where it exists, because
it is better than this and users know it already (see dictatr/shot.py).

    annotate.py --in capture.png --out result.png

Exits 0 with the file written, or 1 if the user pressed Escape. The
geometry lives in dictatr/markup.py, where it can be tested without a
screen; what is left here is drawing and input.
"""

import argparse
import sys
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dictatr.markup import Markup, Shape, fit, rect_from  # noqa: E402

ACCENT = (1.0, 0.29, 0.26)      # the mark colour: red, and only red
DIM = 0.55                      # how much darker outside the selection
LINE = 4.0                      # stroke width, in image pixels
BLOCK = 14                      # mosaic cell, in image pixels

KEYS = {"c": "select", "1": "select", "r": "rect", "2": "rect",
        "a": "arrow", "3": "arrow", "p": "pen", "4": "pen",
        "b": "pixelate", "5": "pixelate"}
HINT = ("drag to select   ·   c crop  r box  a arrow  p pen  b blur"
        "   ·   u undo   ·   enter save   ·   esc cancel")


def _pixelate(cr, src, rect: Rect) -> None:
    """Redact by mosaic rather than blur: a blurred line of text can
    sometimes be reconstructed, and a mosaic this coarse cannot."""
    w, h = max(1, int(rect.w / BLOCK)), max(1, int(rect.h / BLOCK))
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    into = cairo.Context(small)
    into.scale(w / rect.w, h / rect.h)
    into.set_source_surface(src, -rect.x, -rect.y)
    into.paint()
    cr.save()
    cr.rectangle(rect.x, rect.y, rect.w, rect.h)
    cr.clip()
    cr.translate(rect.x, rect.y)
    cr.scale(rect.w / w, rect.h / h)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(cairo.Filter.NEAREST)   # blocks, not smear
    cr.paint()
    cr.restore()


def _arrow(cr, x0, y0, x1, y1) -> None:
    from math import atan2, cos, sin
    head = max(LINE * 4, 18)
    angle = atan2(y1 - y0, x1 - x0)
    # Stop the shaft short of the tip so the two do not overlap into a
    # blob at small sizes.
    cr.move_to(x0, y0)
    cr.line_to(x1 - cos(angle) * head * 0.6, y1 - sin(angle) * head * 0.6)
    cr.stroke()
    cr.move_to(x1, y1)
    for side in (2.6, -2.6):
        cr.line_to(x1 - cos(angle + side) * head,
                   y1 - sin(angle + side) * head)
    cr.close_path()
    cr.fill()


def draw_shapes(cr, src, shapes) -> None:
    """Every mark, in image coordinates. Shared by the screen and the
    saved file so that what is drawn is what is written."""
    cr.set_line_width(LINE)
    cr.set_line_cap(cairo.LineCap.ROUND)
    cr.set_line_join(cairo.LineJoin.ROUND)
    for shape in shapes:
        if shape.kind == "pixelate":
            _pixelate(cr, src, shape.rect)
            continue
        cr.set_source_rgb(*ACCENT)
        if shape.kind == "rect":
            r = shape.rect
            cr.rectangle(r.x, r.y, r.w, r.h)
            cr.stroke()
        elif shape.kind == "arrow":
            _arrow(cr, *shape.points[0], *shape.points[-1])
        else:
            cr.move_to(*shape.points[0])
            for pt in shape.points[1:]:
                cr.line_to(*pt)
            cr.stroke()


def save(src, markup: Markup, out: Path) -> None:
    crop = markup.crop
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                 max(1, int(crop.w)), max(1, int(crop.h)))
    cr = cairo.Context(surface)
    cr.translate(-crop.x, -crop.y)
    cr.set_source_surface(src, 0, 0)
    cr.paint()
    draw_shapes(cr, src, markup.shapes)
    out.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(out))


class Editor(Gtk.ApplicationWindow):
    def __init__(self, app, src, out: Path):
        super().__init__(application=app)
        self.src, self.out = src, out
        self.markup = Markup(src.get_width(), src.get_height())
        self.tool = "select"
        self.drag = None          # points of the stroke in progress
        self.saved = False

        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self.on_draw)
        self.set_child(self.area)
        self.fullscreen()
        self.set_cursor(Gdk.Cursor.new_from_name("crosshair"))

        gesture = Gtk.GestureDrag()
        gesture.connect("drag-begin", self.on_begin)
        gesture.connect("drag-update", self.on_update)
        gesture.connect("drag-end", self.on_end)
        self.area.add_controller(gesture)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    # --- geometry -----------------------------------------------------
    def fit(self):
        return fit(self.markup.width, self.markup.height,
                   self.area.get_width(), self.area.get_height())

    def at(self, x, y):
        return self.fit().to_image(x, y)

    # --- drawing ------------------------------------------------------
    def on_draw(self, _area, cr, width, height):
        f = self.fit()
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        cr.save()
        cr.translate(f.ox, f.oy)
        cr.scale(f.scale, f.scale)
        cr.set_source_surface(self.src, 0, 0)
        cr.paint()
        draw_shapes(cr, self.src, self.markup.shapes)
        if self.tool != "select" and self.drag and len(self.drag) > 1:
            # The stroke in progress, drawn the same way it will be kept.
            draw_shapes(cr, self.src, [Shape(self.tool, list(self.drag))])
        self.draw_selection(cr)
        cr.restore()
        self.draw_hint(cr, width, height)

    def draw_selection(self, cr):
        region = self.pending_region()
        w, h = self.markup.width, self.markup.height
        if region is None:
            return
        # Dim what is being cut away, so the crop is visible as an
        # absence rather than as a line to be looked for.
        cr.set_source_rgba(0, 0, 0, DIM)
        cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
        cr.rectangle(0, 0, w, h)
        cr.rectangle(region.x, region.y, region.w, region.h)
        cr.fill()
        cr.set_source_rgb(*ACCENT)
        cr.set_line_width(2 / max(self.fit().scale, 0.01))
        cr.rectangle(region.x, region.y, region.w, region.h)
        cr.stroke()

    def pending_region(self):
        if self.tool == "select" and self.drag:
            return rect_from(*self.drag[0], *self.drag[-1])
        return self.markup.region

    def draw_hint(self, cr, width, height):
        cr.select_font_face("sans")
        cr.set_font_size(15)
        text = HINT if not self.markup.shapes else HINT.replace(
            "drag to select   ·   ", "")
        w = cr.text_extents(text).width
        x, y = (width - w) / 2, height - 34
        cr.set_source_rgba(0, 0, 0, 0.72)
        cr.rectangle(x - 18, y - 22, w + 36, 34)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.move_to(x, y)
        cr.show_text(text)

    # --- input --------------------------------------------------------
    def on_begin(self, gesture, x, y):
        self.drag = [self.at(x, y)]

    def on_update(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if not ok or self.drag is None:
            return
        point = self.at(sx + dx, sy + dy)
        # A pen keeps every point; everything else is two corners, and
        # keeping the whole path would make the rubber band lag.
        if self.tool == "pen":
            self.drag.append(point)
        else:
            self.drag = [self.drag[0], point]
        self.area.queue_draw()

    def on_end(self, gesture, dx, dy):
        if self.drag is None:
            return
        if self.tool == "select":
            region = rect_from(*self.drag[0], *self.drag[-1])
            self.markup.region = None if region.empty else region
        else:
            self.markup.add(self.tool, self.drag)
        self.drag = None
        self.area.queue_draw()

    def on_key(self, _c, keyval, _code, state):
        name = (Gdk.keyval_name(keyval) or "").lower()
        if name == "escape":
            self.close()
            return True
        if name in ("return", "kp_enter"):
            self.accept()
            return True
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if name == "u" or (name == "z" and ctrl):
            self.markup.undo()
        elif name in KEYS:
            self.tool = KEYS[name]
            # A crosshair for choosing an area, a hand for drawing on it.
            self.set_cursor(Gdk.Cursor.new_from_name(
                "crosshair" if self.tool == "select" else "pencil"))
        else:
            return False
        self.area.queue_draw()
        return True

    def accept(self):
        save(self.src, self.markup, self.out)
        self.saved = True
        self.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", dest="out", required=True)
    args = p.parse_args(argv)
    try:
        src = cairo.ImageSurface.create_from_png(args.src)
    except Exception as e:
        print(f"annotate: cannot read {args.src}: {e}", file=sys.stderr)
        return 1

    app = Gtk.Application(application_id="io.github.rebreda.dictatr.annotate")
    state = {}

    def start(_app):
        win = Editor(app, src, Path(args.out))
        state["win"] = win
        win.present()

    app.connect("activate", start)
    app.run([])
    return 0 if state.get("win") and state["win"].saved else 1


if __name__ == "__main__":
    sys.exit(main())
