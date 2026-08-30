"""Take a screenshot: the desktop's own editor when there is one, the
portal otherwise.

A screenshot on its way to a question is rarely the whole screen. It is
a region, cropped, with the irrelevant half redacted and an arrow drawn
at the thing being asked about. KDE ships a tool that does all of that
in the same drag that selects the area; the Screenshot portal does not,
and hands back a picture, which is correct, universal, and much less
useful.

Three tiers, each a fallback for the one above:

  editor   a desktop tool that selects and annotates in one pass --
           Spectacle, or grim+slurp piped into satty or swappy. Used
           when installed, because it is better than ours and the user
           already knows it.
  ours     the portal grabs the screen and ui/annotate.py does the rest
           (crop, box, arrow, ink, redact). Same on every desktop,
           which is the point of having it.
  portal   the bare capture, if there is no GTK to draw an editor with.

`screenshot = dictatr` forces ours everywhere, `portal` forces the bare
capture, and a command with {path} in it replaces the lot.

Prints the path of the capture, or nothing at all if it was cancelled.
See src/dictatr/dbus.py for the client.
"""

import os
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

from . import dbus, deliver
from .settings import settings

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
APP_ID = "io.github.rebreda.dictatr"  # same id as ui/portal.py

# Editors that select and annotate in one pass, most-preferred first.
# Every entry must write to {path} and write nothing when cancelled,
# which is how a cancelled capture is told from a finished one.
TOOLS = [
    # Plasma's own, and it ships with Plasma, so KDE never falls
    # through to the portal. The region overlay carries crop handles,
    # shapes, ink and blur before you accept it.
    (["spectacle"], ["spectacle", "-b", "-n", "-r", "-o", "{path}"]),
    # wlroots desktops: grim takes the pixels, satty or swappy is the
    # editor. A pipe, so these go through a shell.
    # The path goes in as $1 rather than being pasted into the script,
    # so a directory with a space in it stays one argument.
    (["grim", "slurp", "satty"],
     ["sh", "-c", 'grim -g "$(slurp -d)" - | satty -f - -o "$1" --early-exit',
      "sh", "{path}"]),
    (["grim", "slurp", "swappy"],
     ["sh", "-c", 'grim -g "$(slurp -d)" - | swappy -f - -o "$1"',
      "sh", "{path}"]),
]


def tool_argv(dest: Path, choice: str | None = None) -> list[str] | None:
    """The command to run for *dest*, or None to use the portal.

    *choice* is the `screenshot` setting: "auto" to pick from TOOLS,
    "portal" or "dictatr" to refuse them all (both are handled by
    capture, which has the editor tiers), or a command line with {path}
    in it.
    """
    want = (choice if choice is not None else settings.shot.tool).strip()
    if want in ("portal", "dictatr"):
        return None
    if want and want != "auto":
        return [a.replace("{path}", str(dest)) for a in want.split()]
    for needs, argv in TOOLS:
        if all(shutil.which(n) for n in needs):
            return [a.replace("{path}", str(dest)) for a in argv]
    return None


ANNOTATE = Path(__file__).resolve().parents[2] / "ui" / "annotate.py"


def annotator() -> list[str] | None:
    """How to run our own editor, or None if this box cannot.

    It needs GTK, which the CLI deliberately does not depend on -- a
    headless machine gets the bare capture rather than an import error.
    """
    if not ANNOTATE.exists():
        return None
    py = deliver.gi_python()
    try:
        probe = subprocess.run([py, "-c", "import gi, cairo"],
                               capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [py, str(ANNOTATE)] if probe.returncode == 0 else None


def _request_path(bus, token: str) -> str:
    sender = bus.unique.lstrip(":").replace(".", "_")
    return f"{PATH}/request/{sender}/{token}"


def portal_capture(interactive: bool = True,
                   timeout: float = 120.0) -> Path | None:
    """A screenshot from the portal, or None if cancelled.

    The wait is long because the clock starts when the picker appears
    and choosing is the user's turn, not the machine's."""
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


def _portal_to(dest: Path, interactive: bool, timeout: float) -> Path | None:
    shot = portal_capture(interactive, timeout)
    if shot is None or not shot.exists():
        return None
    # Moved, not copied: KDE's portal saves into ~/Pictures, and a grab
    # taken to ask a question has no business accumulating in someone's
    # screenshots folder. shutil.move because home and the runtime dir
    # are two filesystems, and rename cannot cross them.
    shutil.move(str(shot), dest)
    return dest


def _annotate(raw: Path, dest: Path, argv: list[str],
              timeout: float) -> Path | None:
    """Hand the capture to our editor; None if the user cancelled.

    In and out are different files so that cancelling leaves nothing
    behind: the caller checks for the output, and a leftover raw grab
    at that path would look exactly like a finished screenshot."""
    try:
        r = subprocess.run(argv + ["--in", str(raw), "--out", str(dest)],
                           check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        raw.unlink(missing_ok=True)
    return dest if r.returncode == 0 and dest.exists() else None


def capture(dest: Path, interactive: bool = True,
            timeout: float = 120.0) -> Path | None:
    """A screenshot at *dest*, or None if it was cancelled."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    want = settings.shot.tool.strip()
    if not interactive:
        return _portal_to(dest, False, timeout)

    argv = tool_argv(dest, want)
    if argv:
        try:
            subprocess.run(argv, check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        # Cancelled tools exit cleanly and write nothing, so the file is
        # the only honest answer about whether there is a screenshot.
        return dest if dest.exists() and dest.stat().st_size else None

    ours = annotator() if want in ("auto", "dictatr") else None
    if ours:
        raw = dest.with_suffix(".raw.png")
        # A silent whole-screen grab, because the selecting happens in
        # our editor. Some portals refuse one without a picker; those
        # get the picker, and the editor crops whatever it returns.
        if _portal_to(raw, False, timeout) is None and \
                _portal_to(raw, True, timeout) is None:
            return None
        return _annotate(raw, dest, ours, timeout)
    return _portal_to(dest, True, timeout)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dest = os.environ.get("DICTATE_SHOT")
    if not dest:
        run = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        dest = f"{run}/dictatr/shot-{os.getpid()}.png"
    shot = capture(Path(dest), interactive="--full" not in argv)
    if shot is None:
        return 1
    print(shot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
