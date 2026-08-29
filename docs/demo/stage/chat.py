#!/usr/bin/env python3
"""Chat-messenger prop for demo captures — a Signal-style DM window.

Purely a stage prop: a skeleton sidebar (abstract conversation rows),
preset incoming messages, a focused compose entry (so wtype-typed
dictation lands in it), and Enter / the send button posts the draft as
an outgoing bubble. Styled to the demo palette. No network, nothing real.

--idle stages it as pure backdrop: no focus ring on the composer to
pull the eye off whatever dictatr surface is in front of it.
"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import props  # noqa: E402

# Keep in sync with scenes/chat.py (window placement + camera targets).
WIN_W, WIN_H = 560, 520
SIDEBAR_W = 150

# The one thing on set that DOES spell out real text: the demo's whole
# premise is answering this question by voice.
THREAD = [
    ("out", "running the last release checks now"),
    ("in", "can you summarize where things stand?"),
]

# Sidebar rows: (avatar_label or None for skeleton, active, unread)
ROWS = [
    (None, False, False),
    ("R", True, False),
    (None, False, True),
    (None, False, False),
    (None, False, False),
    (None, False, False),
]

CSS = b"""
window { background: #14151a; }
.sidebar { background: #17181d; }
.side-title { color: #e8eaf1; font-weight: 700; font-size: 16px; }
.search { background: #24262e; border-radius: 9999px; min-height: 30px; }
.row { padding: 9px 12px; border-radius: 10px; }
.row.active { background: #262932; }
.avatar {
  background: #8ab4f8; color: #14151a; font-weight: 700; font-size: 15px;
  border-radius: 9999px; min-width: 38px; min-height: 38px;
}
.avatar.skl { background: #2c2e38; }
.skl { background: #2c2e38; border-radius: 5px; min-height: 9px; }
.skl.dim { background: #23252d; }
.row-name { color: #e8eaf1; font-weight: 600; font-size: 13px; }
.row-preview { color: #7b7e88; font-size: 11px; }
.unread {
  background: #8ab4f8; border-radius: 9999px;
  min-width: 9px; min-height: 9px;
}
.header { background: #1c1d22; padding: 12px 16px; }
.name { color: #e8eaf1; font-weight: 700; font-size: 15px; }
.status { color: #81c995; font-size: 11px; }
.day-pill {
  background: #24262e; color: #a8abb5; font-size: 11px;
  border-radius: 9999px; padding: 3px 12px;
}
.bubble { border-radius: 16px; padding: 9px 14px; font-size: 14px; }
.bubble.in { background: #262932; color: #e8eaf1; }
.bubble.out { background: #8ab4f8; color: #10131c; }
.composer { background: #1c1d22; padding: 10px 12px; }
entry.compose {
  background: #262932; color: #e8eaf1; caret-color: #8ab4f8;
  border: none; border-radius: 9999px; padding: 9px 16px; font-size: 14px;
}
entry.compose placeholder { color: #6b6e78; }
.send {
  background: #8ab4f8; color: #14151a; border-radius: 9999px;
  min-width: 36px; min-height: 36px;
}
"""


def _bar(width, cls="skl"):
    box = Gtk.Box()
    for c in cls.split():
        box.add_css_class(c)
    box.set_size_request(width, 9)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.START)
    return box


class ChatWindow(Gtk.ApplicationWindow):
    def __init__(self, app, idle: bool = False):
        self.idle = idle
        super().__init__(application=app, title="Robin",
                         default_width=WIN_W, default_height=WIN_H)
        props.add_css(self, CSS)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(props.titlebar("Messages"))
        root = Gtk.Box(vexpand=True)
        outer.append(root)
        root.append(self._sidebar())
        convo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        convo.append(self._header())

        # valign END: conversations anchor at the bottom, like any
        # real messenger — no dead space above the composer.
        self.thread = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=10, margin_top=14, margin_bottom=14,
                              margin_start=16, margin_end=16,
                              valign=Gtk.Align.END, vexpand=True)
        pill = Gtk.Label(label="Today")
        pill.add_css_class("day-pill")
        pill.set_halign(Gtk.Align.CENTER)
        self.thread.append(pill)
        for side, text in THREAD:
            self._bubble(side, text)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.thread)
        self.scroll = scroll
        convo.append(scroll)
        convo.append(self._composer())
        root.append(convo)
        self.set_child(outer)

    def _sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_top=14, margin_bottom=14,
                       margin_start=12, margin_end=12)
        side.add_css_class("sidebar")
        side.set_size_request(SIDEBAR_W, -1)

        title = Gtk.Label(label="Chats", xalign=0.0)
        title.add_css_class("side-title")
        side.append(title)
        search = Gtk.Box()
        search.add_css_class("search")
        side.append(search)

        for avatar_label, active, unread in ROWS:
            row = Gtk.Box(spacing=10)
            row.add_css_class("row")
            if active:
                row.add_css_class("active")
            av = Gtk.Label(label=avatar_label or "")
            av.add_css_class("avatar")
            if not avatar_label:
                av.add_css_class("skl")
            row.append(av)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          valign=Gtk.Align.CENTER, hexpand=True)
            if avatar_label:  # the live conversation: real text
                name = Gtk.Label(label="Robin", xalign=0.0)
                name.add_css_class("row-name")
                preview = Gtk.Label(label="can you summarize where thi…",
                                    xalign=0.0)
                preview.add_css_class("row-preview")
                col.append(name)
                col.append(preview)
            else:     # skeleton row: abstract name + preview bars
                col.append(_bar(64))
                col.append(_bar(96, "skl dim"))
            row.append(col)
            if unread:
                dot = Gtk.Box(valign=Gtk.Align.CENTER)
                dot.add_css_class("unread")
                row.append(dot)
            side.append(row)
        return side

    def _header(self):
        box = Gtk.Box(spacing=12)
        box.add_css_class("header")
        avatar = Gtk.Label(label="R")
        avatar.add_css_class("avatar")
        box.append(avatar)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name = Gtk.Label(label="Robin", xalign=0.0)
        name.add_css_class("name")
        status = Gtk.Label(label="online", xalign=0.0)
        status.add_css_class("status")
        col.append(name)
        col.append(status)
        box.append(col)
        return box

    def _bubble(self, side, text):
        lab = Gtk.Label(label=text, wrap=True, xalign=0.0,
                        max_width_chars=24)
        lab.add_css_class("bubble")
        lab.add_css_class(side)
        lab.set_halign(Gtk.Align.START if side == "in" else Gtk.Align.END)
        self.thread.append(lab)
        return lab

    def _composer(self):
        box = Gtk.Box(spacing=10)
        box.add_css_class("composer")
        self.entry = Gtk.Entry(placeholder_text="Message", hexpand=True,
                               can_focus=not self.idle)
        self.entry.add_css_class("compose")
        self.entry.connect("activate", self.on_send)
        box.append(self.entry)
        send = Gtk.Button(icon_name="go-up-symbolic")
        send.add_css_class("send")
        send.connect("clicked", self.on_send)
        box.append(send)
        return box

    def on_send(self, *_):
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")
        self._bubble("out", text)
        GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self):
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False


class ChatApp(Gtk.Application):
    def __init__(self, idle: bool):
        super().__init__(application_id="demo.chat")
        self.idle = idle

    def do_activate(self):
        win = ChatWindow(self, idle=self.idle)
        win.present()
        if not self.idle:
            win.entry.grab_focus()


if __name__ == "__main__":
    ChatApp(idle="--idle" in sys.argv).run([])
