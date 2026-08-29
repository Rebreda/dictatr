#!/usr/bin/env python3
"""Browser prop for demo captures — tabs, URL bar, a forge issue page.

Purely a stage prop: browser chrome (tab strip beside the window
controls, back/forward/reload, a padlocked URL pill) over a dark
issue-tracker page showing the release-candidate checklist the demo's
storyline keeps talking about. No network, nothing real.
"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import props  # noqa: E402

WIN_W, WIN_H = 640, 560

URL = "git.rebreda.dev/dictatr/issues/17"
TABS = [("Release candidate checklist · dictatr", True),
        ("Lemonade docs", False)]
CHECKLIST = [
    (True, "Ship the tray icon rewrite"),
    (True, "Tune listen-mode VAD against room noise"),
    (False, "Tag v0.9-rc1 and build clean artifacts"),
    (False, "Draft short release notes"),
    (False, "Hand RC1 to testers with a feedback deadline"),
]

CSS = b"""
window { background: #17181d; }
.chrome { background: #23252c; }
.tab {
  background: #2c2e38; border-radius: 8px 8px 0 0;
  padding: 6px 14px; color: #e8eaf1; font-size: 12px;
}
.tab.bg { background: transparent; color: #9aa0ac; }
.navrow { background: #23252c; padding: 6px 10px; }
.navbtn { color: #b6bac4; -gtk-icon-size: 14px; }
.urlpill {
  background: #17181d; border-radius: 9999px; padding: 5px 14px;
  color: #cdd1da; font-size: 12px;
}
.urlpill image { color: #81c995; -gtk-icon-size: 11px; }
.page { background: #14151a; }
.issue-title { color: #e8eaf1; font-weight: 700; font-size: 19px; }
.issue-no { color: #7b7e88; font-weight: 400; font-size: 19px; }
.open-pill {
  background: #81c995; color: #10131c; font-weight: 700; font-size: 11px;
  border-radius: 9999px; padding: 4px 12px;
}
.meta { color: #7b7e88; font-size: 12px; }
.card {
  background: #1c1d22; border: 1px solid #2c2e38; border-radius: 10px;
  padding: 14px 16px;
}
.check { color: #e8eaf1; font-size: 13px; }
.check.done { color: #7b7e88; }
.checkbox {
  border: 2px solid #4a4e59; border-radius: 4px;
  min-width: 14px; min-height: 14px;
}
.checkbox.on { background: #8ab4f8; border-color: #8ab4f8; }
.checkbox.on image { color: #10131c; -gtk-icon-size: 10px; }
.label-chip {
  background: alpha(#8ab4f8, 0.2); color: #aecbfa; font-size: 11px;
  border-radius: 9999px; padding: 3px 10px;
}
"""


class BrowserWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Browser",
                         default_width=WIN_W, default_height=WIN_H)
        props.add_css(self, CSS)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self._tab_row())
        outer.append(self._nav_row())
        outer.append(self._page())
        self.set_child(outer)

    def _tab_row(self):
        """Tabs live beside the window controls, browser-style."""
        bar = Gtk.CenterBox()
        bar.add_css_class("chrome")
        tabs = Gtk.Box(spacing=4, margin_top=6, margin_start=8)
        for title, active in TABS:
            tab = Gtk.Box(spacing=8)
            tab.add_css_class("tab")
            if not active:
                tab.add_css_class("bg")
            lab = Gtk.Label(label=title)
            lab.set_max_width_chars(28)
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            tab.append(lab)
            tabs.append(tab)
        plus = Gtk.Image(icon_name="list-add-symbolic", margin_start=6)
        plus.add_css_class("navbtn")
        tabs.append(plus)
        bar.set_start_widget(tabs)
        controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER,
                           margin_end=10, margin_top=4)
        for icon in ("window-minimize-symbolic", "window-maximize-symbolic",
                     "window-close-symbolic"):
            btn = Gtk.Button(icon_name=icon, can_focus=False)
            btn.add_css_class("winbtn")
            controls.append(btn)
        bar.set_end_widget(controls)
        return bar

    def _nav_row(self):
        row = Gtk.Box(spacing=10)
        row.add_css_class("navrow")
        for icon in ("go-previous-symbolic", "go-next-symbolic",
                     "view-refresh-symbolic"):
            row.append(Gtk.Image(icon_name=icon, css_classes=["navbtn"]))
        pill = Gtk.Box(spacing=8, hexpand=True)
        pill.add_css_class("urlpill")
        pill.append(Gtk.Image(icon_name="system-lock-screen-symbolic"))
        url = Gtk.Label(label=URL, xalign=0.0, hexpand=True)
        pill.append(url)
        row.append(pill)
        row.append(Gtk.Image(icon_name="non-starred-symbolic",
                             css_classes=["navbtn"]))
        return row

    def _page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       vexpand=True, margin_top=18, margin_bottom=18,
                       margin_start=22, margin_end=22)
        page.add_css_class("page")

        title_row = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Release candidate checklist", xalign=0.0,
                          wrap=True)
        title.add_css_class("issue-title")
        no = Gtk.Label(label="#17")
        no.add_css_class("issue-no")
        title_row.append(title)
        title_row.append(no)
        page.append(title_row)

        meta_row = Gtk.Box(spacing=10)
        pill = Gtk.Label(label="Open")
        pill.add_css_class("open-pill")
        meta_row.append(pill)
        meta = Gtk.Label(label="rebreda opened yesterday · 2 of 5 done")
        meta.add_css_class("meta")
        meta_row.append(meta)
        chip = Gtk.Label(label="release")
        chip.add_css_class("label-chip")
        meta_row.append(chip)
        page.append(meta_row)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        card.add_css_class("card")
        for done, text in CHECKLIST:
            row = Gtk.Box(spacing=10)
            box = Gtk.Box(valign=Gtk.Align.CENTER)
            box.add_css_class("checkbox")
            if done:
                box.add_css_class("on")
                box.append(Gtk.Image(icon_name="object-select-symbolic"))
            row.append(box)
            lab = Gtk.Label(label=text, xalign=0.0, wrap=True)
            lab.add_css_class("check")
            if done:
                lab.add_css_class("done")
            row.append(lab)
            card.append(row)
        page.append(card)
        wrap = Gtk.Box(vexpand=True)  # page bg fills to the bottom
        page.append(wrap)
        return page


class BrowserApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="demo.browser")

    def do_activate(self):
        BrowserWindow(self).present()


if __name__ == "__main__":
    BrowserApp().run([])
