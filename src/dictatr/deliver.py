"""Get transcribed text to the user: type at the cursor when possible,
clipboard otherwise. Desktop notifications for state feedback.

Typing is a ladder, most portable and least privileged first:
  portal   - RemoteDesktop portal keysym injection (ui/portal_typed.py,
             PyGObject lives there so this package stays stdlib-only);
             used only when a persisted grant token is already stored,
             because a dictation must never pop a permission dialog
             mid-flow. DICTATE_NO_PORTAL=1 skips the tier.
  ydotool  - kernel-level typing, works on any Wayland/X11 desktop
  wl-copy  - Wayland clipboard (wl-clipboard)
  notify-send - freedesktop notifications, any desktop
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import runstate
from .runstate import RUN

_ID_FILE = RUN / "notify-id"
_PORTAL_HELPER = Path(__file__).resolve().parents[2] / "ui" / "portal_typed.py"


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


def _portal_token() -> Path:
    # Keep in sync with token_path() in ui/portal_typed.py.
    state = Path(os.environ.get("XDG_STATE_HOME")
                 or Path.home() / ".local" / "state")
    return state / "dictatr" / "portal-typing-token"


def _gi_python() -> str:
    """A python that can import PyGObject. Plain "python3" is right for a
    packaged install; inside a dev venv it resolves to the venv, which has
    no gi (uv sync builds it without system site-packages), and the portal
    tier would silently drop out. Fall back to the system interpreter."""
    if sys.prefix != sys.base_prefix and Path("/usr/bin/python3").exists():
        return "/usr/bin/python3"
    return "python3"


def _type_portal(text: str) -> bool:
    """RemoteDesktop portal typing. Only attempted with a stored grant
    token: without one the portal would raise a permission dialog in the
    middle of a dictation, so fall through to ydotool instead."""
    if os.environ.get("DICTATE_NO_PORTAL") == "1":
        return False
    if not _portal_token().exists() or not _PORTAL_HELPER.exists():
        return False
    try:
        r = subprocess.run([_gi_python(), str(_PORTAL_HELPER)],
                           input=text.encode(), check=False,
                           capture_output=True, timeout=150)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _type_ydotool(text: str) -> bool:
    if not shutil.which("ydotool"):
        return False
    r = subprocess.run(["ydotool", "type", "--", text], check=False,
                       capture_output=True)
    return r.returncode == 0


def type_text(text: str) -> str | None:
    """Type *text* at the cursor through the best available tier; returns
    the tier that worked ("portal" / "ydotool"), or None when none could.
    The setup wizard uses this to test typing without delivering."""
    if _type_portal(text):
        return "portal"
    if _type_ydotool(text):
        return "ydotool"
    return None


def deliver(text: str, prefer_typing: bool = True) -> str:
    """Deliver *text*; returns "typed" or "clipboard"."""
    runstate.mark_done()   # tray flashes a checkmark
    if prefer_typing and type_text(text):
        notify(f"Typed: {text[:120]}", category="delivery")
        return "typed"
    subprocess.run(["wl-copy"], input=text.encode(), check=False)
    notify(f"Copied to clipboard (Ctrl+V): {text[:120]}", 4000,
           category="delivery")
    return "clipboard"
