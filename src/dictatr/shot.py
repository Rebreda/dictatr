"""Take a screenshot through the portal.

Was spectacle, or grim plus slurp: three programs to depend on, two
codepaths, and a "no region screenshot tool" dead end on any desktop
that had neither. The portal is one call that every Wayland desktop
answers, and it brings its own region selector -- Spectacle's on KDE,
the shell's on GNOME -- so the picker matches the desktop instead of
being whichever tool happened to be installed.

Prints the path of the capture, or nothing at all if the user cancelled
the selection. See src/dictatr/dbus.py for the client.
"""

import os
import shutil
import sys
import urllib.parse
from pathlib import Path

from . import dbus

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
APP_ID = "io.github.rebreda.dictatr"  # same id as ui/portal.py


def _request_path(bus, token: str) -> str:
    sender = bus.unique.lstrip(":").replace(".", "_")
    return f"{PATH}/request/{sender}/{token}"


def capture(interactive: bool = True, timeout: float = 120.0) -> Path | None:
    """A screenshot, or None if it was cancelled or unavailable.

    *interactive* lets the user pick a region; without it the portal
    grabs the whole screen. The wait is long because the clock starts
    when the selector appears and choosing a region is the user's turn,
    not the machine's."""
    bus = dbus.session()
    if bus is None:
        return None
    with bus:
        # Portals >= 1.20 want to know who is calling; older ones have
        # no such interface and say so. Either way the shot works.
        try:
            bus.call(BUS, PATH, "org.freedesktop.host.portal.Registry",
                     "Register", "sa{sv}", (APP_ID, {}), timeout=3)
        except dbus.DBusError:
            pass
        token = f"dictatr{os.getpid()}"
        want = _request_path(bus, token)
        # The subscription has to exist before the call: the portal may
        # answer faster than a second round trip.
        bus.add_match("type='signal',interface='org.freedesktop.portal"
                      f".Request',member='Response',path='{want}'")
        try:
            (handle,) = bus.call(
                BUS, PATH, "org.freedesktop.portal.Screenshot", "Screenshot",
                "sa{sv}", ("", {"handle_token": dbus.Variant("s", token),
                                "interactive": dbus.Variant("b", interactive),
                                "modal": dbus.Variant("b", True)}))
        except dbus.DBusError as e:
            print(f"screenshot portal: {e}", file=sys.stderr)
            return None
        if handle != want:  # pre-1.0 portals mint their own path
            bus.add_match("type='signal',interface='org.freedesktop.portal"
                          f".Request',member='Response',path='{handle}'")
        reply = bus.wait_signal(handle, "Response", timeout)
    if reply is None:
        print("screenshot portal: no response", file=sys.stderr)
        return None
    code, results = reply
    if code == 1:      # the user cancelled, which is not a failure
        return None
    if code:
        print(f"screenshot portal: refused (response {code})",
              file=sys.stderr)
        return None
    uri = results.get("uri", "")
    if not uri.startswith("file://"):
        return None
    return Path(urllib.parse.unquote(uri[7:]))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    shot = capture(interactive="--full" not in argv)
    if shot is None or not shot.exists():
        return 1
    dest = os.environ.get("DICTATE_SHOT")
    if dest:
        # Moved, not copied: KDE's portal saves into ~/Pictures, and a
        # throwaway grab taken to ask the AI a question has no business
        # accumulating in the user's screenshots folder. shutil.move
        # rather than Path.replace because those are two filesystems --
        # home and the runtime dir -- and rename cannot cross them.
        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(shot), target)
        shot = target
    print(shot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
