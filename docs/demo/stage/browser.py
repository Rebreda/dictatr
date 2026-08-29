#!/usr/bin/env python3
"""Browser prop for demo captures — chrome, and a page of nothing.

A stage prop, deliberately mute: the chrome (tab strip beside the
window controls, back/forward/reload, a padlocked URL pill) is what
makes it read as a browser at a glance, so the page behind it is left
as skeleton bars. Nothing here should pull the eye off the dictatr
surface in front of it. No network, no real page.
"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import props  # noqa: E402

WIN_W, WIN_H = 440, 430

CSS = b"""
window { background: #17181d; }
.chrome { background: #23252c; }
.tab { background: #2c2e38; border-radius: 8px 8px 0 0;
       padding: 8px 14px; }
.tab.bg { background: transparent; }
.navrow { background: #23252c; padding: 6px 10px; }
.navbtn { color: #b6bac4; -gtk-icon-size: 14px; }
.urlpill { background: #17181d; border-radius: 9999px; padding: 7px 14px; }
.urlpill image { color: #81c995; -gtk-icon-size: 11px; }
.page { background: #14151a; }
.hero { background: alpha(#8ab4f8, 0.10); border-radius: 8px;
        min-height: 74px; }
"""


class BrowserWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Browser",
                         default_width=WIN_W, default_height=WIN_H)
        props.add_css(self, props.SKELETON_CSS, CSS)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self._tab_row())
        outer.append(self._nav_row())
        outer.append(self._page())
        self.set_child(outer)

    def _tab_row(self):
        """Tabs sit beside the window controls, browser-style."""
        bar = Gtk.CenterBox()
        bar.add_css_class("chrome")
        tabs = Gtk.Box(spacing=4, margin_top=6, margin_start=8)
        for width, active in ((104, True), (72, False)):
            tab = Gtk.Box()
            tab.add_css_class("tab")
            if not active:
                tab.add_css_class("bg")
            title = props.skeleton([width])
            title.set_valign(Gtk.Align.CENTER)
            tab.append(title)
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
        url = props.skeleton([124])
        url.set_valign(Gtk.Align.CENTER)
        pill.append(url)
        row.append(pill)
        row.append(Gtk.Image(icon_name="non-starred-symbolic",
                             css_classes=["navbtn"]))
        return row

    def _page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20,
                       vexpand=True, margin_top=22, margin_bottom=18,
                       margin_start=20, margin_end=20)
        page.add_css_class("page")
        page.append(props.skeleton([210], strong_first=True))
        hero = Gtk.Box()
        hero.add_css_class("hero")
        page.append(hero)
        page.append(props.skeleton([310, 286, 300, 190]))
        page.append(Gtk.Box(vexpand=True))   # page bg fills to the bottom
        return page


class BrowserApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="demo.browser")

    def do_activate(self):
        BrowserWindow(self).present()


if __name__ == "__main__":
    BrowserApp().run([])
