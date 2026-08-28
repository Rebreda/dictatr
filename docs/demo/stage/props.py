"""Shared bits for the stage's prop apps.

Every prop is a plain GTK window drawing its own client-side decoration:
`titlebar()` gives them all the same believable one — centered title,
round minimize/maximize/close controls — so staged windows read as real
desktop apps instead of floating rectangles. One palette, one look.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

# The demo palette, shared with the wallpaper/menu/tray motif.
CSS = b"""
.titlebar { background: #23252c; padding: 7px 10px 7px 16px; }
.tb-title { color: #e8eaf1; font-weight: 700; font-size: 13px; }
.winbtn {
  background: #33363f; border-radius: 9999px;
  min-width: 24px; min-height: 24px; padding: 0;
}
.winbtn image { color: #b6bac4; -gtk-icon-size: 12px; }
"""


def titlebar(title: str) -> Gtk.Widget:
    """A believable CSD titlebar: centered title, window controls."""
    bar = Gtk.CenterBox()
    bar.add_css_class("titlebar")
    lab = Gtk.Label(label=title)
    lab.add_css_class("tb-title")
    bar.set_center_widget(lab)
    controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
    for icon in ("window-minimize-symbolic", "window-maximize-symbolic",
                 "window-close-symbolic"):
        btn = Gtk.Button(icon_name=icon, can_focus=False)
        btn.add_css_class("winbtn")
        controls.append(btn)
    bar.set_end_widget(controls)
    return bar


def add_css(window: Gtk.Window, *sheets: bytes):
    for sheet in (CSS, *sheets):
        provider = Gtk.CssProvider()
        provider.load_from_data(sheet)
        Gtk.StyleContext.add_provider_for_display(
            window.get_display(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
