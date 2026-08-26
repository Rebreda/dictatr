"""Get transcribed text to the user: type at the cursor when possible,
clipboard otherwise. Desktop notifications for state feedback.

All output paths are external tools chosen for portability:
  ydotool  - kernel-level typing, works on any Wayland/X11 desktop (optional)
  wl-copy  - Wayland clipboard (wl-clipboard)
  notify-send - freedesktop notifications, any desktop
"""

import shutil
import subprocess

from .runstate import RUN

_ID_FILE = RUN / "notify-id"


def notify(text: str, ms: int = 2500) -> None:
    """One replaceable notification slot for all of dictatr: each state
    update replaces the previous bubble (-r) instead of stacking, so a
    session reads listening -> transcribing -> delivered as one changing
    notification — never several "Listening…" at once."""
    if not shutil.which("notify-send"):
        return
    cmd = ["notify-send", "-a", "Dictate", "-t", str(ms), "-p"]
    try:
        prev = _ID_FILE.read_text().strip()
        if prev.isdigit() and int(prev):
            cmd += ["-r", prev]
    except OSError:
        pass
    r = subprocess.run(cmd + ["Dictate", text], check=False,
                       capture_output=True, text=True)
    new_id = (r.stdout or "").strip()
    if new_id.isdigit():
        RUN.mkdir(parents=True, exist_ok=True)
        _ID_FILE.write_text(new_id)


def deliver(text: str, prefer_typing: bool = True) -> str:
    """Deliver *text*; returns "typed" or "clipboard"."""
    if prefer_typing and shutil.which("ydotool"):
        r = subprocess.run(["ydotool", "type", "--", text], check=False,
                           capture_output=True)
        if r.returncode == 0:
            notify(f"Typed: {text[:120]}")
            return "typed"
    subprocess.run(["wl-copy"], input=text.encode(), check=False)
    notify(f"Copied to clipboard (Ctrl+V): {text[:120]}", 4000)
    return "clipboard"
