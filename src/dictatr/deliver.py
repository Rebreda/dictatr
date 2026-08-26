"""Get transcribed text to the user: type at the cursor when possible,
clipboard otherwise. Desktop notifications for state feedback.

All output paths are external tools chosen for portability:
  ydotool  - kernel-level typing, works on any Wayland/X11 desktop (optional)
  wl-copy  - Wayland clipboard (wl-clipboard)
  notify-send - freedesktop notifications, any desktop
"""

import shutil
import subprocess


def notify(text: str, ms: int = 2500) -> None:
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", "Dictate", "-t", str(ms), "Dictate", text],
            check=False,
        )


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
