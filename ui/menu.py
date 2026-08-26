#!/usr/bin/env python3
"""Floating radial menu for dictatr — a circle of round action bubbles, in
the spirit of Android's floating assistant ball.

GTK4 + PyGObject only (system packages on Fedora/GNOME, fine on KDE, macOS
via Homebrew). Single-instance via GApplication: launching it again while
open closes it, so the hotkey toggles the menu instead of stacking copies.

Positioning: Wayland compositors don't let normal apps place windows at the
global cursor, so by default the circle appears centered. If the optional
KDE-specific helpers are present, it anchors at the pointer instead:
    sudo dnf install gtk4-layer-shell kdotool
(kdotool reads the cursor position from KWin; gtk4-layer-shell lets the
window place itself. Both are skipped silently when missing — e.g. GNOME.)
"""

import math
import re
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

DICTATE = str(Path(__file__).resolve().parent.parent / "bin" / "dictate")

# (icon, tooltip, dictate args; None = file picker)
ACTIONS = [
    ("audio-input-microphone-symbolic", "Dictate (type at cursor)", ["type"]),
    ("edit-copy-symbolic", "Dictate to clipboard", ["clip"]),
    ("folder-music-symbolic", "Transcribe audio file…", None),
    ("process-stop-symbolic", "Cancel recording", ["cancel"]),
]

SIZE = 230          # window edge
BUBBLE = 52         # satellite diameter
CENTER_BUBBLE = 60  # hub diameter
RADIUS = 78         # orbit radius

CSS = b"""
window { background: transparent; }
.hub, .bubble {
  border-radius: 9999px;
  border: 1px solid alpha(#ffffff, 0.10);
  background: alpha(#1c1d22, 0.93);
  transition: background 130ms ease, border-color 130ms ease;
}
.hub image { color: #8ab4f8; }
.bubble image { color: #e8eaf1; }
.bubble:hover, .bubble:focus-visible {
  background: alpha(#8ab4f8, 0.28);
  border-color: alpha(#8ab4f8, 0.6);
}
.hub:hover { background: alpha(#f28b82, 0.25); }
"""


def cursor_position():
    """Global pointer position via kdotool (KWin only); None elsewhere."""
    try:
        out = subprocess.run(["kdotool", "getmouselocation", "--shell"],
                             capture_output=True, text=True, timeout=2).stdout
        x = int(re.search(r"X=(\d+)", out)[1])
        y = int(re.search(r"Y=(\d+)", out)[1])
        return x, y
    except Exception:
        return None


def try_anchor_at_cursor(win):
    """Place *win* at the pointer using gtk4-layer-shell, if available."""
    pos = cursor_position()
    if pos is None:
        return False
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LS
    except (ValueError, ImportError):
        return False
    LS.init_for_window(win)
    LS.set_layer(win, LS.Layer.OVERLAY)
    LS.set_keyboard_mode(win, LS.KeyboardMode.ON_DEMAND)
    for edge in (LS.Edge.TOP, LS.Edge.LEFT):
        LS.set_anchor(win, edge, True)
    LS.set_margin(win, LS.Edge.LEFT, max(0, pos[0] - SIZE // 2))
    LS.set_margin(win, LS.Edge.TOP, max(0, pos[1] - SIZE // 2))
    return True


class Radial(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, decorated=False, resizable=False)
        self.set_default_size(SIZE, SIZE)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        fixed = Gtk.Fixed()
        cx = cy = SIZE // 2

        hub = Gtk.Button(icon_name="audio-input-microphone-symbolic",
                         tooltip_text="Close")
        hub.add_css_class("hub")
        hub.set_size_request(CENTER_BUBBLE, CENTER_BUBBLE)
        hub.connect("clicked", lambda *_: self.close())
        fixed.put(hub, cx - CENTER_BUBBLE / 2, cy - CENTER_BUBBLE / 2)

        self.buttons = []
        for i, (icon, tip, _) in enumerate(ACTIONS):
            angle = -math.pi / 2 + i * (2 * math.pi / len(ACTIONS))
            bx = cx + RADIUS * math.cos(angle) - BUBBLE / 2
            by = cy + RADIUS * math.sin(angle) - BUBBLE / 2
            b = Gtk.Button(icon_name=icon, tooltip_text=f"{tip}  [{i + 1}]")
            b.add_css_class("bubble")
            b.set_size_request(BUBBLE, BUBBLE)
            b.connect("clicked", self.on_action, i)
            fixed.put(b, bx, by)
            self.buttons.append(b)

        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=140, child=fixed)
        self.set_child(self.revealer)
        GLib.idle_add(self.revealer.set_reveal_child, True)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        try_anchor_at_cursor(self)

    def on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_0 + len(ACTIONS):
            self.on_action(None, keyval - Gdk.KEY_1)
            return True
        return False

    def on_action(self, _btn, index):
        args = ACTIONS[index][2]
        if args is None:
            self.pick_file()
            return
        subprocess.Popen([DICTATE, *args])
        self.close()

    def pick_file(self):
        dialog = Gtk.FileDialog(title="Transcribe audio file")
        f = Gtk.FileFilter()
        f.set_name("Audio files")
        for m in ("audio/x-wav", "audio/mpeg", "audio/mp4", "audio/ogg",
                  "audio/flac", "audio/webm"):
            f.add_mime_type(m)
        dialog.set_default_filter(f)

        def done(d, res):
            try:
                gfile = d.open_finish(res)
            except GLib.Error:
                self.close()
                return
            subprocess.Popen([DICTATE, "file", gfile.get_path()])
            self.close()

        dialog.open(self, None, done)


class MenuApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="ca.qxg.dictatr.menu")
        self.win = None

    def do_activate(self):
        # Single instance + toggle: a second hotkey press lands here in the
        # primary process; close the open menu instead of stacking another.
        if self.win is not None:
            self.win.close()
            self.win = None
            return
        self.win = Radial(self)
        self.win.connect("close-request", self._closed)
        self.win.present()

    def _closed(self, *_):
        self.win = None
        return False


def main():
    sys.exit(MenuApp().run(None))


if __name__ == "__main__":
    main()
