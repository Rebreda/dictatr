#!/usr/bin/env python3
"""Word-processor prop for demo captures — toolbar, and a blank page.

A stage prop, deliberately mute: the formatting toolbar and the bright
sheet on a dark desk are what make it read as a word processor, so the
document itself is skeleton bars. A scene that actually dictates INTO
the page can pass --live for a real (empty, focused) text view instead.
No file IO, nothing real.
"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import props  # noqa: E402

WIN_W, WIN_H = 420, 520

CSS = b"""
window { background: #23252c; }
.toolbar { background: #2b2d34; padding: 7px 12px; }
.tool { color: #b6bac4; -gtk-icon-size: 14px; }
.tool-sep { background: #4a4e59; min-width: 1px; margin: 3px 6px; }
.fontpill { background: #17181d; border-radius: 6px; padding: 6px 12px; }
.deskspace { background: #17181d; }
.sheet { background: #f6f5f1; border-radius: 3px; }
/* On the white sheet the skeleton inverts: ink, not light. */
.sheet .skel { background: alpha(#2a2c33, 0.16); }
.sheet .skel.strong { background: alpha(#2a2c33, 0.34); }
textview.doc, textview.doc text { background: #f6f5f1; color: #2a2c33; }
"""


class WriterWindow(Gtk.ApplicationWindow):
    def __init__(self, app, live: bool = False):
        super().__init__(application=app, title="Writer",
                         default_width=WIN_W, default_height=WIN_H)
        props.add_css(self, props.SKELETON_CSS, CSS)
        self.text = None

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(props.titlebar("Untitled 1 — Writer"))
        outer.append(self._toolbar())

        desk = Gtk.Box(vexpand=True)
        desk.add_css_class("deskspace")
        sheet = Gtk.Box(hexpand=True, margin_top=18,
                        margin_start=22, margin_end=22)
        sheet.add_css_class("sheet")
        sheet.append(self._live_page() if live else self._skeleton_page())
        desk.append(sheet)
        outer.append(desk)
        self.set_child(outer)

    def _live_page(self):
        """A real, empty page — for scenes that dictate into it."""
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD,
                                 left_margin=34, right_margin=30,
                                 top_margin=32, hexpand=True)
        self.text.add_css_class("doc")
        return self.text

    def _skeleton_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22,
                       hexpand=True, margin_top=30, margin_start=28,
                       margin_end=24)
        page.append(props.skeleton([150], strong_first=True))
        page.append(props.skeleton([260, 244, 252, 230, 180]))
        page.append(props.skeleton([252, 236, 160]))
        page.append(Gtk.Box(vexpand=True))
        return page

    def _toolbar(self):
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("toolbar")
        for width in (74, 22):   # font family, size
            pill = Gtk.Box(spacing=6)
            pill.add_css_class("fontpill")
            field = props.skeleton([width])
            field.set_valign(Gtk.Align.CENTER)
            pill.append(field)
            pill.append(Gtk.Image(icon_name="pan-down-symbolic",
                                  css_classes=["tool"]))
            bar.append(pill)
        bar.append(Gtk.Box(css_classes=["tool-sep"]))
        for icon in ("format-text-bold-symbolic",
                     "format-text-italic-symbolic",
                     "format-text-underline-symbolic"):
            bar.append(Gtk.Image(icon_name=icon, css_classes=["tool"]))
        bar.append(Gtk.Box(css_classes=["tool-sep"]))
        for icon in ("format-justify-left-symbolic",
                     "format-justify-center-symbolic",
                     "format-justify-fill-symbolic"):
            bar.append(Gtk.Image(icon_name=icon, css_classes=["tool"]))
        bar.append(Gtk.Box(css_classes=["tool-sep"]))
        for icon in ("insert-image-symbolic", "insert-link-symbolic"):
            bar.append(Gtk.Image(icon_name=icon, css_classes=["tool"]))
        return bar


class WriterApp(Gtk.Application):
    def __init__(self, live: bool):
        super().__init__(application_id="demo.writer")
        self.live = live

    def do_activate(self):
        win = WriterWindow(self, live=self.live)
        win.present()
        if win.text is not None:
            win.text.grab_focus()


if __name__ == "__main__":
    WriterApp(live="--live" in sys.argv).run([])
