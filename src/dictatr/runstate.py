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
