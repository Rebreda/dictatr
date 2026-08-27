"""Get transcribed text to the user: type at the cursor when possible,
clipboard otherwise. Desktop notifications for state feedback.

All output paths are external tools chosen for portability:
  ydotool  - kernel-level typing, works on any Wayland/X11 desktop (optional)
  wl-copy  - Wayland clipboard (wl-clipboard)
  notify-send - freedesktop notifications, any desktop
"""

import shutil
import subprocess

from . import runstate
from .runstate import RUN

_ID_FILE = RUN / "notify-id"


def notify(text: str, ms: int = 2500, category: str = "state") -> None:
    """Category-aware notifications, each toggleable in settings.

    "state" chatter (listening / transcribing) shares one replaceable
    bubble (-r) so it never stacks — but Plasma doesn't re-pop a bubble
    it already showed, which buried important messages too when
    everything shared the slot. So every other category (delivery,
    answers, toggles, errors) fires a fresh popup, after closing any
    lingering state bubble."""
    from .settings import settings
    if not getattr(settings.notify, category, True):
        return
    if not shutil.which("notify-send"):
        return
    cmd = ["notify-send", "-a", "Dictate", "-t", str(ms)]
    prev = None
    try:
        prev = _ID_FILE.read_text().strip()
    except OSError:
        pass
    if category == "state":
        cmd.append("-p")
        if prev and prev.isdigit() and int(prev):
            cmd += ["-r", prev]
    elif prev and prev.isdigit() and int(prev):
        # A fresh popup is about to say something newer than the state
        # bubble; close the stale "Transcribing…" instead of leaving it.
        subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.freedesktop.Notifications",
             "--object-path", "/org/freedesktop/Notifications",
             "--method", "org.freedesktop.Notifications.CloseNotification",
             prev],
            check=False, capture_output=True)
        _ID_FILE.unlink(missing_ok=True)
    r = subprocess.run(cmd + ["Dictate", text], check=False,
                       capture_output=True, text=True)
    new_id = (r.stdout or "").strip()
    if category == "state" and new_id.isdigit():
        RUN.mkdir(parents=True, exist_ok=True)
        _ID_FILE.write_text(new_id)


def deliver(text: str, prefer_typing: bool = True) -> str:
    """Deliver *text*; returns "typed" or "clipboard"."""
    runstate.mark_done()   # tray flashes a checkmark
    if prefer_typing and shutil.which("ydotool"):
        r = subprocess.run(["ydotool", "type", "--", text], check=False,
                           capture_output=True)
        if r.returncode == 0:
            notify(f"Typed: {text[:120]}", category="delivery")
            return "typed"
    subprocess.run(["wl-copy"], input=text.encode(), check=False)
    notify(f"Copied to clipboard (Ctrl+V): {text[:120]}", 4000,
           category="delivery")
    return "clipboard"
