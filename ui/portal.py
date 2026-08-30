"""Talking to xdg-desktop-portal, in one place.

Three surfaces used to carry their own copy of the request dance: the
tray for global shortcuts, the wizard for binding them, and the typing
helper for RemoteDesktop. They drifted, which is how the private-bus fix
below landed in two of them and not the third.

Two things are subtle enough to be worth owning once:

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
