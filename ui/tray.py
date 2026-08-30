#!/usr/bin/python3
"""System tray icon for dictatr: mic state at a glance, quick actions.

A StatusNotifierItem + com.canonical.dbusmenu implemented directly over
Gio DBus — no GTK window, no libappindicator, no new dependencies. Works
on any SNI host (KDE Plasma, most Wayland bars); GNOME needs its
AppIndicator extension, as with every tray icon.

The tray also hosts the global hotkeys via the GlobalShortcuts desktop
portal (Plasma 6, GNOME 48+): it binds the four default shortcuts at
startup and spawns the same commands the .desktop files run. Where the
portal is missing (wlroots) it logs once and does nothing; the legacy
kglobalshortcutsrc path (bin/dictate-hotkeys) still works there.

The icon tracks the runstate pidfiles so recording is unambiguous:
red while a hotkey session records, with a badge for where the
transcript goes (caret = typed at cursor, clipboard, chat bubble = ask);
green while the always-on listener is live; dark when idle. The menu's
checkbox mirrors the listener, and left-click opens the radial menu.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import (GdkPixbuf, Gio,  # noqa: E402
                           GLib, GLibUnix)

REPO = Path(__file__).resolve().parent.parent
DICTATE = str(REPO / "bin" / "dictate")
ICONS = REPO / "ui" / "icons"
sys.path.insert(0, str(REPO / "src"))
from dictatr import deliver, gestures, runstate  # noqa: E402
from dictatr.settings import settings  # noqa: E402

sys.path.insert(0, str(REPO / "ui"))
import portal  # noqa: E402
from shortcuts import SHORTCUTS as PORTAL_SHORTCUTS  # noqa: E402

BUS_NAME = "io.github.rebreda.dictatr.tray"
APP_ID = portal.APP_ID
PORTAL_BUS = portal.BUS
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

CONTROL_XML = """<node>
 <interface name="io.github.rebreda.dictatr.Shortcuts">
  <method name="Rebind"/>
  <method name="ActiveApp">
   <arg type="s" direction="in"/><arg type="s" direction="in"/>
  </method>
  <method name="Trace"><arg type="s" direction="in"/></method>
 </interface>
</node>"""

KWIN_SCRIPT = REPO / "ui" / "kwin" / "activewindow.js"
KWIN_PLUGIN = "dictatr-activewindow"

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
    (13, "Set up dictatr", "setup"),
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
            bus.register_object_with_closures2(
                path, iface, handler, self.on_get_prop, None)
        Gio.bus_watch_name_on_connection(
            bus, "org.kde.StatusNotifierWatcher",
            Gio.BusNameWatcherFlags.NONE, self._register, None)
        self._watch_state()

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

    # --- noticing, rather than asking ----------------------------------
    #
    # This used to be a 500ms timer: two wakeups a second for the life of
    # the session, to catch a handful of transitions a day. Measured, it
    # was 0.1% of a core and 2.25 wakeups/sec while the machine sat
    # untouched, and it was the only thing dictatr did when nobody was
    # using it.
    #
    # Everything _state() reads is a file this application writes, so
    # inotify can say when to look. The one thing a file cannot report is
    # a process dying without tidying up after itself -- for that, each
    # live pid gets a pidfd, which becomes readable exactly when it exits.
    # Between them there is nothing left to poll for.

    def _watch_state(self):
        runstate.RUN.mkdir(parents=True, exist_ok=True)
        self._pidfds = {}
        self._flash = None
        self._monitor = Gio.File.new_for_path(
            str(runstate.RUN)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._monitor.connect("changed", lambda *_: self._restate())
        self._restate()

    def _restate(self):
        """Re-read the state and tell the panel if it moved."""
        self._watch_pids()
        self._schedule_flash()
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
        return False

    def _watch_pids(self):
        """A pidfd per live session, so a crash is an event too.

        Without this the icon would sit on "recording" until something
        else touched the run directory, because a session killed outright
        leaves its pidfile behind and inotify has nothing to say.
        """
        want = set()
        for pidfile in (runstate.DICTATE_PID, runstate.LISTEN_PID):
            pid = runstate.live_pid(pidfile)
            if pid is None:
                continue
            want.add(pid)
            if pid in self._pidfds:
                continue
            try:
                fd = os.pidfd_open(pid)
            except (AttributeError, OSError):
                continue          # older kernel: inotify alone still works
            src = GLibUnix.fd_add_full(
                GLib.PRIORITY_DEFAULT, fd, GLib.IOCondition.IN,
                lambda _fd, _cond, p=pid: self._died(p))
            self._pidfds[pid] = (fd, src)
        for pid in [p for p in self._pidfds if p not in want]:
            self._drop_pidfd(pid)

    def _drop_pidfd(self, pid):
        fd, src = self._pidfds.pop(pid, (None, None))
        if src is not None:
            GLib.source_remove(src)
        if fd is not None:
            os.close(fd)

    def _died(self, pid):
        self._drop_pidfd(pid)
        self._restate()
        return False

    def _schedule_flash(self):
        """The checkmark is the one transition nothing signals: it ends
        because time passed. One timer, armed when the marker appears."""
        if self._flash is not None:
            return
        age = runstate.done_age()
        if age is None or age >= DONE_FLASH_S:
            return
        self._flash = GLib.timeout_add(
            int((DONE_FLASH_S - age) * 1000) + 50, self._flash_over)

    def _flash_over(self):
        self._flash = None
        self._restate()
        return False

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
                spawn([DICTATE, "type"])
            else:
                spawn([str(REPO / "bin" / "dictate-menu")])
        elif method == "SecondaryActivate":
            spawn([DICTATE, "listen", "--toggle"])
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
            spawn([str(REPO / "bin" / "dictate-chat")])
        elif action == "settings":
            spawn([str(REPO / "bin" / "dictate-menu"),
                              "--settings"])
        elif action == "setup":
            spawn([str(REPO / "bin" / "dictate-setup")])
        elif action == "gc":
            def run():
                r = subprocess.run([DICTATE, "gc"], capture_output=True,
                                   text=True)
                deliver.notify(f"Archive gc: {r.stdout.strip() or 'done'}",
                               category="toggles")
            GLib.Thread.new("gc", run)
        elif isinstance(action, list):
            spawn([DICTATE, *action])


def _name_taken(bus) -> bool:
    try:
        return bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (BUS_NAME,)), None,
            Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    except GLib.Error:
        return False


def first_run() -> None:
    """Offer the wizard the first time the tray ever starts. It writes a
    setup_done key whether it finishes or is dismissed, so this fires
    exactly once; "Set up dictatr" in the menu runs it again on demand."""
    if os.environ.get("DICTATE_NO_SETUP") == "1":
        return
    try:
        from dictatr.settings import setup_seen
        if setup_seen():
            return
        # A moment behind the tray so the icon is already in the bar when
        # the window appears, and the session is done starting up.
        GLib.timeout_add_seconds(
            3, lambda: (spawn(
                [str(REPO / "bin" / "dictate-setup")]), False)[1])
    except Exception as e:
        print(f"dictatr tray: could not offer setup: {e}", file=sys.stderr)


def _kwin(bus, method, sig, args):
    """Call KWin's scripting service; False when this is not KWin."""
    try:
        bus.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                      method, GLib.Variant(sig, args), None,
                      Gio.DBusCallFlags.NONE, 3000, None)
        return True
    except GLib.Error:
        return False


# Which shortcut each gesture performs. The names are the ones in
# ui/shortcuts.py, so a gesture and a hotkey are the same catalogue.
GESTURE_KEYS = {
    "shake-v": "gesture_shake_v",
    "shake-h": "gesture_shake_h",
    "circle-cw": "gesture_circle_cw",
    "circle-ccw": "gesture_circle_ccw",
}


# One gesture is one action. Two seconds is long enough that a shake
# which keeps going does not fire twice, short enough that a deliberate
# second gesture is not swallowed.
GESTURE_COOLDOWN_S = 2.0
_last_gesture = 0.0


def on_trace(blob: str) -> None:
    """Judge a stretch of pointer movement the compositor handed over.

    The compositor only knows that something happened; what it was, and
    whether it was anything at all, is decided here."""
    width, height, points = gestures.parse(blob)
    if not points or not height:
        return
    name, numbers = gestures.judge(points, height)
    if "gesture" in settings.debug:
        _log(f"trace {name or '-'}: {numbers}")
    if name is None:
        return

    # Only this end knows a gesture was actually recognised, so the
    # cooldown that matters lives here. The compositor's quiet period
    # paces how often it offers a trace; it cannot tell an accepted
    # shake from a stretch of mouse movement that judged as nothing.
    global _last_gesture
    now = time.monotonic()
    if now - _last_gesture < GESTURE_COOLDOWN_S:
        if "gesture" in settings.debug:
            _log(f"gesture {name} ignored: within cooldown")
        return

    action = getattr(settings.gestures, GESTURE_KEYS[name].replace(
        "gesture_", ""), "")
    if not action:
        return
    _last_gesture = now
    cmd = next((c for sid, _d, _t, c in PORTAL_SHORTCUTS if sid == action),
               None)
    if cmd:
        _log(f"gesture {name} -> {action}")
        spawn(cmd, start_new_session=True)


# Everything the tray launches, kept until it is reaped. The tray
# outlives its children by design, so nothing else will: an unreaped
# one lingers as a zombie that still answers kill(pid, 0), which is
# indistinguishable from a live session to anything watching pidfiles.
def spawn(cmd, **kw):
    """Launch something, and reap it the moment it exits.

    The tray outlives its children by design, so nothing else will: an
    unreaped one lingers as a zombie that still answers kill(pid, 0),
    which is indistinguishable from a live session to anything watching
    the pidfiles. A child watch is SIGCHLD, so this costs nothing while
    nothing is exiting -- where the sweep it replaces ran twice a second
    for the life of the process to notice something that happens a
    handful of times a day.
    """
    proc = subprocess.Popen(cmd, **kw)

    def reaped(_pid, status, _proc=proc):
        # GLib has already waited on it. Tell Popen the answer so it
        # neither waits again nor complains at collection.
        _proc.returncode = status
        return False

    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, proc.pid, reaped)
    return proc


def _log(msg: str) -> None:
    """One line to the tray's own output: `./dev logs` in a checkout,
    `journalctl --user -t dictatr-tray` for an installed one."""
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def _is_ours(pid: str) -> bool:
    """Whether a window belongs to dictatr itself.

    Focusing the menu or the chat must not overwrite the app you were
    working in: that app is the whole point of the answer. Our surfaces
    are layer-shell, so they have no app id to recognise them by and all
    report as the interpreter; the pid is what tells them apart."""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            return b"dictatr" in f.read()
    except (OSError, ValueError):
        return False


def watch_apps(bus) -> None:
    """Ask KWin to tell us which application is focused.

    Wayland has no client-side answer, so the knowledge has to come from
    the compositor: ui/kwin/activewindow.js calls back into this tray on
    every focus change. Reloaded each startup so a changed script takes
    effect, and unloaded on exit so nothing of ours outlives the tray."""
    if not KWIN_SCRIPT.exists():
        return
    _kwin(bus, "unloadScript", "(s)", (KWIN_PLUGIN,))
    if _kwin(bus, "loadScript", "(ss)", (str(KWIN_SCRIPT), KWIN_PLUGIN)):
        _kwin(bus, "start", "()", ())


def unwatch_apps(bus) -> None:
    _kwin(bus, "unloadScript", "(s)", (KWIN_PLUGIN,))
    runstate.APP.unlink(missing_ok=True)


QT_MODIFIERS = {"SHIFT": 0x02000000, "CTRL": 0x04000000,
                "ALT": 0x08000000, "META": 0x10000000,
                "SUPER": 0x10000000, "LOGO": 0x10000000}


def _qt_keycode(trigger: str) -> int | None:
    """Portal trigger ("CTRL+ALT+d") to the Qt key code kglobalaccel
    speaks. Qt numbers plain keys by their uppercase ASCII value, so a
    letter or space needs no table. None when it is anything fancier."""
    parts = trigger.upper().split("+")
    code = 0
    for mod in parts[:-1]:
        if mod not in QT_MODIFIERS:
            return None
        code |= QT_MODIFIERS[mod]
    key = parts[-1]
    if key == "SPACE":
        return code | 0x20
    if len(key) == 1:
        return code | ord(key)
    return None


class Shortcuts:
    """Global hotkeys via the GlobalShortcuts portal, hosted here since
    the tray is the resident process. Fully asynchronous: the portal's
    request pattern returns a Request object path immediately and the
    real reply arrives as a Response signal on it, so nothing blocks the
    tray loop. Any failure logs one stderr line and gives up; the legacy
    kglobalshortcutsrc path (bin/dictate-hotkeys) still works."""

    IFACE = portal.GLOBAL_SHORTCUTS

    def __init__(self):
        self.bus = portal.session_bus()
        portal.register(self.bus)
        self.req = portal.Requests(self.bus, self.IFACE)
        self.session = None
        self._open()

    def _open(self):
        self._request("CreateSession", "(a{sv})", (),
                      {"session_handle_token": GLib.Variant("s", "dictatr")},
                      self._on_session)

    def close(self):
        """Close the portal session before exiting.

        Killed without this, the session is torn down asynchronously and
        the teardown can land after the replacement tray has bound,
        taking the new registration with it: hotkeys then work or not
        depending on which side of the race won, which is what a restart
        loop looks like from the outside."""
        old, self.session = self.session, None
        if not old:
            return
        try:
            self.bus.call_sync(PORTAL_BUS, old,
                               "org.freedesktop.portal.Session", "Close",
                               None, None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass

    def rebind(self):
        """Take the live key match back.

        A second session binding the same ids wins them, and the setup
        wizard's session does exactly that and then closes: the keys are
        left pointing at a session nobody owns, so kglobalaccel still
        knows the shortcut but pressing it reaches no one. Reopening our
        session makes this process the owner again."""
        _log("shortcuts: rebinding, taking the keys back")
        self.close()
        self._open()

    def _fail(self, msg: str) -> None:
        _log(f"shortcuts: unavailable: {msg}")
        print(f"dictatr tray: portal hotkeys unavailable: {msg}; "
              "bind shortcuts in your desktop instead (dictate-hotkeys "
              "on KDE)", file=sys.stderr)

    def _request(self, method, sig, args, options, cb):
        err = self.req.call(method, sig, args, options, cb)
        if err is not None:
            self._fail(err)

    def _on_session(self, code, results):
        session = results.get("session_handle")
        if code or not session:
            self._fail(f"CreateSession refused ({code})")
            return
        self.session = session
        _log(f"shortcuts: session open ({session})")
        self.bus.signal_subscribe(PORTAL_BUS, self.IFACE, "Activated",
                                  portal.PATH, None,
                                  Gio.DBusSignalFlags.NONE, self._activated)
        # Deactivated is the chord coming back up. Typing waits on this:
        # see CHORD in runstate.py.
        self.bus.signal_subscribe(PORTAL_BUS, self.IFACE, "Deactivated",
                                  portal.PATH, None,
                                  Gio.DBusSignalFlags.NONE, self._deactivated)
        shorts = [(sid, {"description": GLib.Variant("s", desc),
                         "preferred_trigger": GLib.Variant("s", trig)})
                  for sid, desc, trig, _ in PORTAL_SHORTCUTS]
        self._request("BindShortcuts", "(oa(sa{sv})sa{sv})",
                      (session, shorts, ""), {}, self._on_bound)

    def _on_bound(self, code, results):
        if code:
            self._fail(f"BindShortcuts refused ({code})")
            return
        triggers = {sid: (meta.get("trigger_description") or "")
                    for sid, meta in results.get("shortcuts", [])}
        # Anything the portal accepted without putting a key on. KDE does
        # that when something already holds the combination (including
        # legacy entries about to be retired), and for a shortcut added
        # to a component it already knows; either way it never revisits
        # them once they are free, so assign those directly.
        unbound = [sid for sid, _d, _t, _c in PORTAL_SHORTCUTS
                   if not triggers.get(sid)]
        _log(f"shortcuts: bound {len(PORTAL_SHORTCUTS) - len(unbound)}"
             f"/{len(PORTAL_SHORTCUTS)}"
             + (f", unbound {' '.join(unbound)}" if unbound else ""))
        self._retire_legacy()
        if unbound and not self._assign_kde(unbound):
            self._fail("the portal bound no keys")

    def _assign_kde(self, only=None) -> bool:
        """Assign the preferred triggers through kglobalaccel, the same
        call System Settings makes when a user picks a shortcut by hand.
        KDE registers portal shortcuts there but leaves them unbound
        (kglobalshortcutsrc shows `none`) whenever it saw a conflict."""
        done = False
        for sid, desc, trig, _cmd in PORTAL_SHORTCUTS:
            if only is not None and sid not in only:
                continue
            keycode = _qt_keycode(trig)
            if keycode is None:
                continue
            try:
                self.bus.call_sync(
                    "org.kde.kglobalaccel", "/kglobalaccel",
                    "org.kde.KGlobalAccel", "setForeignShortcut",
                    GLib.Variant("(asai)",
                                 ([APP_ID, sid, "dictatr", desc], [keycode])),
                    None, Gio.DBusCallFlags.NONE, 3000, None)
            except GLib.Error:
                return False
            done = True
        if done:
            print("dictatr tray: portal left the hotkeys unbound; assigned "
                  "the defaults via kglobalaccel", file=sys.stderr)
        return done

    def _deactivated(self, _bus, _sender, _path, _iface, _sig, params):
        session = params.unpack()[0]
        if session == self.session:
            runstate.chord_down(False)

    def _activated(self, _bus, _sender, _path, _iface, _sig, params):
        session, sid, _ts, _opts = params.unpack()
        if session != self.session:
            return
        runstate.chord_down(True)
        for wanted, _desc, _trig, cmd in PORTAL_SHORTCUTS:
            if wanted == sid:
                spawn(cmd)
                return

    def _retire_legacy(self):
        """With the portal bind live, a leftover kglobalshortcutsrc entry
        from bin/dictate-hotkeys would fire the same command a second
        time per press (KDE honours both). Remove the legacy entries so
        exactly one path is live; dictate-hotkeys can recreate them if
        the user ever leaves the portal behind."""
        cfg = Path(os.environ.get("XDG_CONFIG_HOME",
                                  Path.home() / ".config"))
        try:
            text = (cfg / "kglobalshortcutsrc").read_text()
        except OSError:
            return
        if "dictate.desktop" not in text or not shutil.which("kwriteconfig6"):
            return
        for group in ("dictate.desktop", "dictate-menu.desktop",
                      "dictate-cancel.desktop", "dictate-listen.desktop"):
            subprocess.run(["kwriteconfig6", "--file", "kglobalshortcutsrc",
                            "--group", "services", "--group", group,
                            "--key", "_launch", "--delete"],
                           check=False, capture_output=True)
        print("dictatr tray: hotkeys now bound via the desktop portal; "
              "removed legacy kglobalshortcutsrc entries", file=sys.stderr)


def main():
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    if _name_taken(bus):
        # Ask before claiming, so a duplicate exits now and says why.
        # The callback below is the race that check cannot cover, and
        # there loop.quit() is not enough: the name can be lost before
        # loop.run() begins, and quitting a loop that never ran leaves
        # a second tray alive next to the first.
        print("dictatr tray already running", file=sys.stderr)
        return 1

    def lost(*_):
        print("dictatr tray already running", file=sys.stderr)
        os._exit(0)

    Gio.bus_own_name_on_connection(bus, BUS_NAME,
                                   Gio.BusNameOwnerFlags.NONE, None, lost)
    Tray(bus, loop)
    first_run()
    shortcuts = None
    if os.environ.get("DICTATE_NO_PORTAL") != "1":
        try:
            shortcuts = Shortcuts()
        except Exception as e:
            print(f"dictatr tray: portal hotkeys unavailable: {e}",
                  file=sys.stderr)

    # Rebind: the setup wizard calls this after its own bind, which would
    # otherwise leave the keys owned by its closed session.
    def on_call(_bus, _sender, _path, _iface, method, params, inv):
        if method == "Rebind" and shortcuts is not None:
            shortcuts.rebind()
        elif method == "ActiveApp":
            app, pid = params.unpack()
            if not _is_ours(pid):
                runstate.write_app(app)
        elif method == "Trace":
            on_trace(params.unpack()[0])
        inv.return_value(None)

    iface = Gio.DBusNodeInfo.new_for_xml(CONTROL_XML).interfaces[0]
    bus.register_object_with_closures2("/Shortcuts", iface,
                                      on_call, None, None)

    watch_apps(bus)

    def shutdown(*_):
        if shortcuts is not None:
            shortcuts.close()
        unwatch_apps(bus)
        loop.quit()
        return GLib.SOURCE_REMOVE

    for sig in (signal.SIGTERM, signal.SIGINT):
        GLibUnix.signal_add(GLib.PRIORITY_HIGH, sig, shutdown)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
