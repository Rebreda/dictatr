"""Pidfiles under $XDG_RUNTIME_DIR/dictatr.

Two cooperating processes coordinate through these: the hotkey session
(DICTATE_PID, one per dictation/ask) and the always-on listener
(LISTEN_PID, see listen.py). The listener pauses while DICTATE_PID is
live so an utterance is never archived twice.
"""

import os
import time
from pathlib import Path

RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "dictatr"
DICTATE_PID = RUN / "pid"
LISTEN_PID = RUN / "listen.pid"
# What the live hotkey session will do with the transcript
# ("type" | "clip" | "ask") — the tray shows it while recording.
MODE = RUN / "mode"
# Touched on successful delivery; the tray flashes a checkmark while
# this file is fresh.
DONE = RUN / "done"
# The tray touches this while a global-shortcut chord is held down and
# removes it on release. Typing waits for it to clear: injecting keysyms
# while the real Ctrl+Alt are still down desyncs the compositor's
# modifier tracking, and the desktop is left acting as though Ctrl is
# stuck. Dictation is a toggle, so the second press is still held when
# the transcript is ready.
CHORD = RUN / "chord"
# The focused application's class, kept current by the tray (a KWin
# script reports it; see ui/kwin/activewindow.js). Dictations record it
# so recall can prefer what you said while in the same app, and ask mode
# can say "the user is in code" without guessing.
APP = RUN / "app"


def live_pid(pidfile: Path) -> int | None:
    try:
        pid = int(pidfile.read_text())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError,
            PermissionError):
        return None


def write_pid(pidfile: Path) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()))


def write_mode(mode: str) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    MODE.write_text(mode)


def read_mode() -> str | None:
    try:
        return MODE.read_text().strip() or None
    except OSError:
        return None


def mark_done() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    DONE.touch()


def done_age() -> float | None:
    """Seconds since the last successful delivery, None if never."""
    try:
        return time.time() - DONE.stat().st_mtime
    except OSError:
        return None


def chord_down(held: bool) -> None:
    if held:
        RUN.mkdir(parents=True, exist_ok=True)
        CHORD.touch()
    else:
        CHORD.unlink(missing_ok=True)


def chord_held(stale_after: float = 5.0) -> bool:
    """True while a hotkey chord is down. A missed release (the tray
    died mid-press) would otherwise block typing forever, so the flag
    expires."""
    try:
        age = time.time() - CHORD.stat().st_mtime
    except OSError:
        return False
    return age < stale_after


def write_app(app: str) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    APP.write_text(app)


def read_app() -> str | None:
    """The focused app's class, or None when nothing is tracking it
    (no tray, no KWin, another desktop)."""
    try:
        return APP.read_text().strip() or None
    except OSError:
        return None
