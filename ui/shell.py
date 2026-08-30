#!/usr/bin/python3
"""The one process every surface lives in.

There used to be three: the menu, the chat and the wizard, each spawning
the next and closing on the following line. That is why they could only
ever be tethered together — a quarter of a second of nothing between one
overlay disappearing and the next appearing, papered over with a line
drawn across the gap. radial.Overlay's own docstring had already noticed
they were the same object underneath.

Here they are scenes in one graph, in one window, in one process, so
going from the menu to the chat is a zoom rather than a handoff. Nothing
is spawned and nothing has to be told where the last thing was.

Single-instance the way the tray is: a bus name, checked before it is
claimed and fatal to lose. The shims in bin/ became clients that call
Open on it, so everything that launched a surface before — the tray, the
portal shortcuts, the .desktop entry, the demo scenes — still does,
without knowing anything changed.

    dictate-shell                 # the resident process
    dictate-menu                  # a client: Open("menu")
    dictate-menu --standalone     # the old widget surface, its own process

--standalone stays because some things genuinely need a surface in a
process of their own: tools/enginepreview and the demo stage each hand
one a scratch XDG_CONFIG_HOME, and settings.py snapshots its config at
import, so a resident shell cannot be given one.
"""

import signal
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ui"))
sys.path.insert(0, str(REPO / "src"))

import canvas as C  # noqa: E402
import graph as G  # noqa: E402
import radial  # noqa: E402
import scenes  # noqa: E402

BUS_NAME = "io.github.rebreda.dictatr.shell"
BUS_PATH = "/Shell"
SHELL_XML = """<node>
 <interface name="io.github.rebreda.dictatr.Shell">
  <method name="Open"><arg type="s" direction="in"/></method>
  <method name="Toggle"><arg type="s" direction="in"/></method>
  <method name="Dismiss"/>
 </interface>
</node>"""


class Shell(Gtk.ApplicationWindow):
    """One fullscreen overlay, one canvas, every scene."""

    def __init__(self, app):
        super().__init__(application=app, decorated=False)
        radial.apply_css()
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            str(REPO / "ui" / "icons" / "theme"))

        self.graph = G.Graph(scenes.menu_nodes())
        self.canvas = C.Canvas(self.graph, "menu",
                               on_activate=lambda n: scenes.activate(n, self))
        self.canvas.install_gestures()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)

        self.ov = radial.Overlay(self, self.canvas, (900, 900),
                                 on_place=self._placed)
        self.showing = False
        if not self.ov.start():
            self.set_default_size(900, 900)
            self.set_child(self.canvas)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: self.dismiss() or True)

    # --- the scene -------------------------------------------------------
    def open(self, scene="menu"):
        """Show *scene*, or move to it if the shell is already up.

        Moving rather than reopening is the whole point: if the surface
        is already on screen, going somewhere else in it is a zoom.
        """
        if scene in self.graph:
            self.canvas.path.go(scene)
            self.canvas.announce()
        self.showing = True
        self.canvas.animate()
        self.present()

    def toggle(self, scene="menu"):
        if self.showing and self.get_visible():
            self.dismiss()
        else:
            self.open(scene)

    def dismiss(self):
        """Put the surface away without ending the process.

        The scene keeps its shape, so opening it again is the same scene
        rather than a fresh one — which is what a resident shell buys.
        """
        self.showing = False
        self.set_visible(False)
        return False

    def _placed(self, *_):
        self.canvas.animate()

    def _on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            if not self.canvas.back():
                self.dismiss()
            self.canvas.animate()
            return True
        if keyval in (Gdk.KEY_BackSpace,):
            self.canvas.back()
            self.canvas.animate()
            return True
        import radial_layout as L
        index = L.digit_index(keyval, len(self.canvas.path.node.children))
        if index is None:
            return False
        if self.canvas.can_enter(index):
            self.canvas.enter(index)
        else:
            self.canvas.activate(index)
        self.canvas.animate()
        return True


class ShellApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.rebreda.dictatr.shellapp",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.win = None

    def do_activate(self):
        # hold(): the shell outlives its window, which is the difference
        # between a surface and a process that happens to draw one.
        self.hold()
        if self.win is None:
            self.win = Shell(self)
        if not self._served:
            # Registering the same object path twice is an error, and
            # activation can happen more than once.
            self._served = True
            self._serve()
        self.win.open("menu")

    _served = False

    def _serve(self):
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def on_call(_bus, _sender, _path, _iface, method, params, inv):
            if method == "Open":
                self.win.open(params.unpack()[0] or "menu")
            elif method == "Toggle":
                self.win.toggle(params.unpack()[0] or "menu")
            elif method == "Dismiss":
                self.win.dismiss()
            inv.return_value(None)

        iface = Gio.DBusNodeInfo.new_for_xml(SHELL_XML).interfaces[0]
        bus.register_object_with_closures2(BUS_PATH, iface, on_call,
                                           None, None)
        Gio.bus_own_name_on_connection(
            bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None,
            lambda *_: sys.exit("dictatr shell: another shell owns the bus"))


def call(method, scene="menu", start=True):
    """Client half: ask the resident shell, starting it if it is not up.

    Returns False when there is no shell and none could be started, so a
    shim can fall back to opening the old surface in its own process.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(BUS_NAME, BUS_PATH,
                      "io.github.rebreda.dictatr.Shell", method,
                      GLib.Variant("(s)", (scene,)) if method != "Dismiss"
                      else None,
                      None, Gio.DBusCallFlags.NONE, 900, None)
        return True
    except GLib.Error:
        pass
    if not start:
        return False
    import subprocess
    subprocess.Popen([str(REPO / "bin" / "dictate-shell")],
                     start_new_session=True)
    # The shell opens the menu itself when it starts, so a first call
    # that had to launch it has already been served by doing so.
    return True


def main():
    if "--client" in sys.argv:
        scene = next((a for a in sys.argv[1:] if not a.startswith("-")),
                     "menu")
        method = "Toggle" if "--toggle" in sys.argv else "Open"
        return 0 if call(method, scene) else 1
    for sig in (signal.SIGINT,):
        GLibUnix.signal_add(GLib.PRIORITY_HIGH, sig,
                            lambda *_: (sys.exit(0), False)[1])
    return ShellApp().run([])


if __name__ == "__main__":
    sys.exit(main())
