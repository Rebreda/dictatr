"""Talking to xdg-desktop-portal, in one place.

Three surfaces used to carry their own copy of the request dance: the
tray for global shortcuts, the wizard for binding them, and the typing
helper for RemoteDesktop. They drifted, which is how the private-bus fix
below landed in two of them and not the third.

Two things are subtle enough to be worth owning once, and the
GlobalShortcuts bind dance now lives here as well -- it was in the
wizard, which is the surface that happens to run it, not the thing that
knows how it works.

*The connection.* The portal ties an app id to the connection that first
speaks to it, and a connection can only be registered once. Anything
that reaches the portal on the shared session bus first (GTK does, for
the colour scheme) leaves it associated with an empty id, and
GlobalShortcuts then refuses the session with "An app id is required".
A connection of our own guarantees Register goes first.

*The request pattern.* A portal method returns a Request object path and
the real answer arrives later as a Response signal on it, so the
subscription has to exist before the call. Pre-1.0 portals mint their
own path instead of honouring the token, so the subscription may have to
move. Callers want this two ways: a resident process wants a callback
(nothing may block its loop), a one-shot helper wants an answer.

gi is imported inside the functions: the pure helpers in portal_typed.py
are unit-tested in a venv that has no PyGObject.
"""

import os

APP_ID = "io.github.rebreda.dictatr"
BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
REGISTRY_IFACE = "org.freedesktop.host.portal.Registry"

GLOBAL_SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"


def session_bus():
    """A private session-bus connection, so Register is its first word."""
    from gi.repository import Gio
    addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    return Gio.DBusConnection.new_for_address_sync(
        addr,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)


def register(bus, app_id: str = APP_ID) -> None:
    """Self-identify to portal >= 1.20. Older portals lack the
    interface and a second call is refused, so every error is ignored."""
    from gi.repository import Gio, GLib
    try:
        bus.call_sync(BUS, PATH, REGISTRY_IFACE, "Register",
                      GLib.Variant("(sa{sv})", (app_id, {})), None,
                      Gio.DBusCallFlags.NONE, 3000, None)
    except GLib.Error:
        pass


class PortalError(RuntimeError):
    pass


class Requests:
    """The request dance for one interface on one connection."""

    def __init__(self, bus, iface: str):
        self.bus = bus
        self.iface = iface
        self._sender = bus.get_unique_name().lstrip(":").replace(".", "_")
        self._n = 0

    def _token(self) -> tuple[str, str]:
        self._n += 1
        token = f"dictatr{os.getpid()}_{self._n}"
        return token, f"{PATH}/request/{self._sender}/{token}"

    def _subscribe(self, path, handler):
        from gi.repository import Gio
        return self.bus.signal_subscribe(
            BUS, REQUEST_IFACE, "Response", path, None,
            Gio.DBusSignalFlags.NONE, handler)

    def call(self, method, sig, args, options, on_response, timeout_s=None):
        """Async: *on_response* gets (code, results). Returns an error
        string when the call itself failed, None when it is in flight."""
        from gi.repository import Gio, GLib
        token, path = self._token()
        state = {"done": False, "sub": None}

        def responded(_b, _s, _p, _i, _sg, params):
            if state["done"]:
                return
            state["done"] = True
            self.bus.signal_unsubscribe(state["sub"])
            on_response(*params.unpack())

        state["sub"] = self._subscribe(path, responded)
        opts = dict(options, handle_token=GLib.Variant("s", token))
        try:
            handle = self.bus.call_sync(
                BUS, PATH, self.iface, method,
                GLib.Variant(sig, tuple(args) + (opts,)), None,
                Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error as e:
            self.bus.signal_unsubscribe(state["sub"])
            return e.message
        if handle != path:   # pre-1.0 portals mint their own path
            self.bus.signal_unsubscribe(state["sub"])
            state["sub"] = self._subscribe(handle, responded)
        if timeout_s:
            def expired():
                if not state["done"]:
                    state["done"] = True
                    self.bus.signal_unsubscribe(state["sub"])
                    on_response(2, {"timeout": True})
                return False
            GLib.timeout_add_seconds(int(timeout_s), expired)
        return None

    def call_sync(self, method, sig, args, options, timeout_s=10):
        """Blocking: returns the results dict, raises PortalError."""
        from gi.repository import GLib
        loop = GLib.MainLoop()
        out = {}

        def responded(code, results):
            out["code"], out["results"] = code, results
            loop.quit()

        err = self.call(method, sig, args, options, responded)
        if err is not None:
            raise PortalError(f"{method}: {err}")
        src = GLib.timeout_add(int(timeout_s * 1000),
                               lambda: (loop.quit(), False)[1])
        loop.run()
        if "code" not in out:
            raise PortalError(f"{method}: no response within {timeout_s}s")
        GLib.source_remove(src)
        if out["code"]:
            what = "cancelled" if out["code"] == 1 else "refused"
            raise PortalError(f"{method}: {what} (response {out['code']})")
        return out["results"]


def version(iface: str):
    """The portal interface's version, or None if it is not there.

    The wizard asks before offering to bind anything: a desktop with no
    GlobalShortcuts portal needs to be told that, not shown a button
    that fails."""
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return bus.call_sync(
            BUS, PATH, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (iface, "version")), None,
            Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    except GLib.Error:
        return None


class ShortcutBinder:
    """One GlobalShortcuts bind dance: CreateSession, BindShortcuts, read
    back the triggers the desktop actually assigned, then close the
    session. Async, so the desktop's dialog does not freeze the caller.

    A binder's session is temporary on purpose. Binding and listening
    are different jobs with different lifetimes: the tray holds a
    session open for as long as it wants to hear the shortcuts fire,
    while the wizard only wants the desktop to write them down.

    *done* is called with (ok, shortcuts, message)."""

    def __init__(self, done, shortcuts):
        self.done = done
        self.shortcuts = shortcuts
        self.bus = session_bus()
        self.req = Requests(self.bus, GLOBAL_SHORTCUTS)
        self.session = None

    def run(self):
        from gi.repository import GLib  # noqa: F811
        register(self.bus)
        self._request("CreateSession", "(a{sv})", (),
                      {"session_handle_token":
                       GLib.Variant("s", "dictatrbind")},
                      self._on_session)

    def _request(self, method, sig, args, options, cb, timeout_s=180):
        err = self.req.call(method, sig, args, options, cb, timeout_s)
        if err is not None:
            self.done(False, [], err)

    def _on_session(self, code, results):
        from gi.repository import GLib
        self.session = results.get("session_handle")
        if code or not self.session:
            self.done(False, [], f"the session was refused ({code})")
            return
        shorts = [(sid, {"description": GLib.Variant("s", desc),
                         "preferred_trigger": GLib.Variant("s", trig)})
                  for sid, desc, trig, _cmd in self.shortcuts]
        self._request("BindShortcuts", "(oa(sa{sv})sa{sv})",
                      (self.session, shorts, ""), {}, self._on_bound)

    def _on_bound(self, code, results):
        self._close()
        if code:
            self.done(False, [], f"the request was refused ({code})")
            return
        self.done(True, results.get("shortcuts") or [], "")

    def _close(self):
        from gi.repository import Gio, GLib
        if not self.session:
            return
        try:
            self.bus.call_sync(BUS, self.session,
                               "org.freedesktop.portal.Session", "Close",
                               None, None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            pass
