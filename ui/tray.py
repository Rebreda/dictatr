#!/usr/bin/env python3
"""System tray icon for dictatr: mic state at a glance, quick actions.

A StatusNotifierItem + com.canonical.dbusmenu implemented directly over
Gio DBus — no GTK window, no libappindicator, no new dependencies. Works
on any SNI host (KDE Plasma, most Wayland bars); GNOME needs its
AppIndicator extension, as with every tray icon.

The icon tracks the runstate pidfiles so recording is unambiguous:
red while a hotkey session records, with a badge for where the
transcript goes (caret = typed at cursor, clipboard, chat bubble = ask);
green while the always-on listener is live; dark when idle. The menu's
checkbox mirrors the listener, and left-click opens the radial menu.
"""

import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DICTATE = str(REPO / "bin" / "dictate")
ICONS = REPO / "ui" / "icons"
sys.path.insert(0, str(REPO / "src"))
from dictatr import runstate  # noqa: E402

BUS_NAME = "io.github.rebreda.dictatr.tray"
# Theme-icon fallbacks, used only if the bundled pixmaps fail to load.
ICON_IDLE = "audio-input-microphone"
ICON_LIVE = "media-record"

# state -> (pixmap basename, tooltip)
STATES = {
    "idle": ("tray-idle", "Voice dictation"),
    "listen": ("tray-live", "Always-on capture is LIVE"),
    "rec-type": ("tray-rec-type", "Recording: will type at the cursor"),
    "rec-clip": ("tray-rec-clip", "Recording: will copy to the clipboard"),
    "rec-ask": ("tray-rec-ask", "Recording an ask-mode question"),
    "done": ("tray-done", "Transcript delivered"),
}
DONE_FLASH_S = 2.5   # how long the checkmark lingers after delivery


def load_pixmaps(name: str) -> list:
    """PNG -> SNI IconPixmap entries: (w, h, ARGB32 network byte order)."""
    out = []
    for size in (24, 48):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(str(ICONS / f"{name}-{size}.png"))
        except GLib.Error:
            return []
        if not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
        data = pb.get_pixels()
        argb = bytearray()
        for y in range(h):
            row = data[y * stride:y * stride + w * 4]
            for x in range(0, w * 4, 4):
                r, g, b, a = row[x:x + 4]
                argb += bytes((a, r, g, b))
        out.append((w, h, bytes(argb)))
    return out

SNI_XML = """<node>
 <interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <method name="Activate"><arg type="i"/><arg type="i"/></method>
  <method name="SecondaryActivate"><arg type="i"/><arg type="i"/></method>
  <method name="ContextMenu"><arg type="i"/><arg type="i"/></method>
  <method name="Scroll"><arg type="i"/><arg type="s"/></method>
  <signal name="NewIcon"/>
  <signal name="NewToolTip"/>
 </interface>
</node>"""

MENU_XML = """<node>
 <interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <property name="Status" type="s" access="read"/>
  <method name="GetLayout">
   <arg type="i" direction="in"/><arg type="i" direction="in"/>
   <arg type="as" direction="in"/>
   <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
  </method>
  <method name="GetGroupProperties">
   <arg type="ai" direction="in"/><arg type="as" direction="in"/>
   <arg type="a(ia{sv})" direction="out"/>
  </method>
  <method name="GetProperty">
   <arg type="i" direction="in"/><arg type="s" direction="in"/>
   <arg type="v" direction="out"/>
  </method>
  <method name="Event">
   <arg type="i" direction="in"/><arg type="s" direction="in"/>
   <arg type="v" direction="in"/><arg type="u" direction="in"/>
  </method>
  <method name="AboutToShow">
   <arg type="i" direction="in"/><arg type="b" direction="out"/>
  </method>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
 </interface>
</node>"""

# (id, label, action) — checkbox id 5 mirrors the listener pidfile;
# ids 10-12 only appear while a hotkey session is recording.
MENU_ITEMS = [
    (10, "Stop recording (deliver)", ["type"]),
    (11, "Discard recording", ["cancel"]),
    (12, "", None),
    (1, "Dictate (type at cursor)", ["type"]),
    (2, "Dictate to clipboard", ["clip"]),
    (3, "Ask the AI (voice chat)", "chat"),
    (4, "", None),  # separator
    (5, "Always-on capture", ["listen", "--toggle"]),
    (6, "Clean up archive now", "gc"),
    (7, "", None),
    (8, "Settings", "settings"),
    (9, "Quit tray", "quit"),
]
RECORDING_IDS = {10, 11, 12}


class Tray:
    def __init__(self, bus: Gio.DBusConnection, loop: GLib.MainLoop):
        self.bus = bus
        self.loop = loop
        self.state = self._state()
        self.revision = 1
        self.pixmaps = {name: load_pixmaps(base)
                        for name, (base, _) in STATES.items()}
        for xml, path, handler in ((SNI_XML, "/StatusNotifierItem", self.on_sni),
                                   (MENU_XML, "/MenuBar", self.on_menu)):
            iface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
            bus.register_object(path, iface, handler, self.on_get_prop, None)
        Gio.bus_watch_name_on_connection(
            bus, "org.kde.StatusNotifierWatcher",
            Gio.BusNameWatcherFlags.NONE, self._register, None)
        GLib.timeout_add(500, self._poll)

    @staticmethod
    def _state() -> str:
        """A recording hotkey session outranks everything; a fresh
        delivery flashes a checkmark; then the always-on listener."""
        if runstate.live_pid(runstate.DICTATE_PID) is not None:
            mode = runstate.read_mode() or "type"
            return f"rec-{mode}" if f"rec-{mode}" in STATES else "rec-type"
        age = runstate.done_age()
        if age is not None and age < DONE_FLASH_S:
            return "done"
        if runstate.live_pid(runstate.LISTEN_PID) is not None:
            return "listen"
        return "idle"

    @property
    def live(self) -> bool:
        return runstate.live_pid(runstate.LISTEN_PID) is not None

    def _register(self, bus, name, owner):
        bus.call(name, "/StatusNotifierWatcher",
                 "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                 GLib.Variant("(s)", (bus.get_unique_name(),)),
                 None, Gio.DBusCallFlags.NONE, -1, None, None)

    def _poll(self):
        state = self._state()
        if state != self.state:
            self.state = state
            self.revision += 1
            for sig in ("NewIcon", "NewToolTip"):
                self.bus.emit_signal(None, "/StatusNotifierItem",
                                     "org.kde.StatusNotifierItem", sig, None)
            self.bus.emit_signal(None, "/MenuBar", "com.canonical.dbusmenu",
                                 "LayoutUpdated",
                                 GLib.Variant("(ui)", (self.revision, 0)))
        return True

    # --- properties ----------------------------------------------------
    def on_get_prop(self, _bus, _sender, _path, _iface, name):
        state = self.state
        fallback_icon = ICON_IDLE if state == "idle" else ICON_LIVE
        if name == "IconName":
            # Empty when we serve pixmaps: hosts prefer a non-empty name.
            if self.pixmaps[state]:
                return GLib.Variant("s", "")
            return GLib.Variant("s", fallback_icon)
        if name == "IconPixmap":
            return GLib.Variant("a(iiay)", self.pixmaps[state])
        if name == "ToolTip":
            return GLib.Variant("(sa(iiay)ss)",
                                (fallback_icon, [],
                                 "Dictate", STATES[state][1]))
        fixed = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "dictatr"),
            "Title": GLib.Variant("s", "Dictate"),
            "Status": GLib.Variant("s", "Active"),
            "Menu": GLib.Variant("o", "/MenuBar"),
            "ItemIsMenu": GLib.Variant("b", False),
            "Version": GLib.Variant("u", 3),
        }
        return fixed.get(name)

    # --- StatusNotifierItem --------------------------------------------
    def on_sni(self, _bus, _sender, _path, _iface, method, _params, inv):
        if method == "Activate":
            if self.state.startswith("rec"):
                # Click the red icon = stop & deliver, like the hotkey.
                subprocess.Popen([DICTATE, "type"])
            else:
                subprocess.Popen([str(REPO / "bin" / "dictate-menu")])
        elif method == "SecondaryActivate":
            subprocess.Popen([DICTATE, "listen", "--toggle"])
        inv.return_value(None)

    # --- com.canonical.dbusmenu ----------------------------------------
    def _props(self, mid, label, action):
        p = ({"type": GLib.Variant("s", "separator")} if action is None
             else {"label": GLib.Variant("s", label)})
        if mid == 5:
            p["toggle-type"] = GLib.Variant("s", "checkmark")
            p["toggle-state"] = GLib.Variant("i", 1 if self.live else 0)
        if mid in RECORDING_IDS:
            p["visible"] = GLib.Variant(
                "b", self.state.startswith("rec"))
        return p

    def on_menu(self, _bus, _sender, _path, _iface, method, params, inv):
        if method == "GetLayout":
            children = [
                GLib.Variant("(ia{sv}av)", (i, self._props(i, lab, act), []))
                for i, lab, act in MENU_ITEMS]
            root = (0, {"children-display": GLib.Variant("s", "submenu")},
                    children)
            inv.return_value(GLib.Variant("(u(ia{sv}av))",
                                          (self.revision, root)))
        elif method == "GetGroupProperties":
            ids = set(params[0]) or {i for i, _, _ in MENU_ITEMS}
            rows = [(i, self._props(i, lab, act))
                    for i, lab, act in MENU_ITEMS if i in ids]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))
        elif method == "GetProperty":
            mid, name = params
            for i, lab, act in MENU_ITEMS:
                if i == mid:
                    v = self._props(i, lab, act).get(name)
                    inv.return_value(GLib.Variant("(v)", (v,)) if v else None)
                    return
            inv.return_value(None)
        elif method == "Event":
            if params[1] == "clicked":
                self.on_click(params[0])
            inv.return_value(None)
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        else:
            inv.return_value(None)

    def on_click(self, mid):
        action = next((a for i, _, a in MENU_ITEMS if i == mid), None)
        if action == "quit":
            self.loop.quit()
        elif action == "chat":
            subprocess.Popen([str(REPO / "bin" / "dictate-chat")])
        elif action == "settings":
            subprocess.Popen([str(REPO / "bin" / "dictate-menu"),
                              "--settings"])
        elif action == "gc":
            def run():
                r = subprocess.run([DICTATE, "gc"], capture_output=True,
                                   text=True)
                subprocess.run(["notify-send", "-a", "Dictate", "Archive gc",
                                r.stdout.strip() or "done"], check=False)
            GLib.Thread.new("gc", run)
        elif isinstance(action, list):
            subprocess.Popen([DICTATE, *action])


def main():
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def lost(*_):
        print("dictatr tray already running", file=sys.stderr)
        loop.quit()

    Gio.bus_own_name_on_connection(bus, BUS_NAME,
                                   Gio.BusNameOwnerFlags.NONE, None, lost)
    Tray(bus, loop)
    loop.run()


if __name__ == "__main__":
    main()
