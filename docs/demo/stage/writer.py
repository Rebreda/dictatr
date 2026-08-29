#!/usr/bin/env python3
"""Word-processor prop for demo captures — toolbar and a white page.

Purely a stage prop: titlebar, a formatting toolbar (font, size, bold /
italic / underline, alignment) and a bright document page with a draft
of the release notes the demo's storyline is working toward. The page
is a Gtk.TextView, so staged typing lands in it if a scene wants that.
No file IO, nothing real.
"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import props  # noqa: E402

WIN_W, WIN_H = 620, 640

DOC = """dictatr 0.9 — release notes (draft)

Highlights
•  Tray icon rewrite: plain-DBus StatusNotifierItem, works on
   Plasma and most Wayland bars
•  Always-on capture tuned against measured room noise
•  Floating voice chat: a continued conversation with the AI,
   entirely by voice

Fixes
•  Muted microphones are detected instead of listened to
•  Realtime deltas no longer double mid-utterance
"""

CSS = b"""
window { background: #23252c; }
.toolbar { background: #2b2d34; padding: 6px 12px; }
.tool { color: #b6bac4; -gtk-icon-size: 14px; }
.tool-sep { background: #4a4e59; min-width: 1px; margin: 2px 6px; }
.fontpill {
  background: #17181d; border-radius: 6px; padding: 3px 12px;
  color: #cdd1da; font-size: 12px;
}
.deskspace { background: #17181d; }
.sheet { background: #f6f5f1; border-radius: 3px; }
textview.doc, textview.doc text { background: #f6f5f1; color: #2a2c33; }
"""


class WriterWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Writer",
                         default_width=WIN_W, default_height=WIN_H)
        props.add_css(self, CSS)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(props.titlebar("release-notes.odt — Writer"))
        outer.append(self._toolbar())

        desk = Gtk.Box(vexpand=True)
        desk.add_css_class("deskspace")
        sheet = Gtk.Box(hexpand=True,
                        margin_top=18, margin_bottom=0,
                        margin_start=26, margin_end=26)
        sheet.add_css_class("sheet")
        self.text = Gtk.TextView(editable=True, cursor_visible=True,
                                 wrap_mode=Gtk.WrapMode.WORD,
                                 left_margin=34, right_margin=30,
                                 top_margin=30, hexpand=True)
        self.text.add_css_class("doc")
        buf = self.text.get_buffer()
        buf.set_text(DOC)
        buf.place_cursor(buf.get_end_iter())
        sheet.append(self.text)
        desk.append(sheet)
        outer.append(desk)
        self.set_child(outer)

    def _toolbar(self):
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("toolbar")
        font = Gtk.Box(spacing=6)
        font.add_css_class("fontpill")
        font.append(Gtk.Label(label="Cantarell"))
        font.append(Gtk.Image(icon_name="pan-down-symbolic",
                              css_classes=["tool"]))
        bar.append(font)
        size = Gtk.Box(spacing=6)
        size.add_css_class("fontpill")
        size.append(Gtk.Label(label="11"))
        size.append(Gtk.Image(icon_name="pan-down-symbolic",
                              css_classes=["tool"]))
        bar.append(size)
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
        bar.append(Gtk.Image(icon_name="insert-image-symbolic",
                             css_classes=["tool"]))
        bar.append(Gtk.Image(icon_name="insert-link-symbolic",
                             css_classes=["tool"]))
        return bar


class WriterApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="demo.writer")

    def do_activate(self):
        win = WriterWindow(self)
        win.present()
        win.text.grab_focus()


if __name__ == "__main__":
    WriterApp().run([])
