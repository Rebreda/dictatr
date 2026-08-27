"""Pidfiles under $XDG_RUNTIME_DIR/dictatr.

Two cooperating processes coordinate through these: the hotkey session
(DICTATE_PID, one per dictation/ask) and the always-on listener
(LISTEN_PID, see listen.py). The listener pauses while DICTATE_PID is
live so an utterance is never archived twice.
"""

import os
from pathlib import Path

RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "dictatr"
DICTATE_PID = RUN / "pid"
LISTEN_PID = RUN / "listen.pid"
# What the live hotkey session will do with the transcript
# ("type" | "clip" | "ask") — the tray shows it while recording.
MODE = RUN / "mode"


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
