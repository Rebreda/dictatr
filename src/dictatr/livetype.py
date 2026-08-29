"""Type a transcript at the cursor while it is still being revised.

The realtime engine hands out a running transcript that grows and gets
rewritten as more audio arrives ("their" becoming "there" a word later),
so live typing is a reconciliation, not an append: work out how much of
what is already on screen still matches, erase the rest, type the
remainder.

LiveTyper owns one long-lived portal session (ui/portal_typed.py
--stream) for the whole utterance, because a session costs a round trip
and partials arrive every few words. The diff itself is a pure function,
so the interesting part is testable without a compositor.
"""

import json
import subprocess
from pathlib import Path

from . import runstate

HELPER = Path(__file__).resolve().parents[2] / "ui" / "portal_typed.py"


def edit_to(typed: str, target: str) -> tuple[int, str]:
    """(characters to erase, text to type) to turn *typed* into *target*.

    Only the common prefix survives: a revision that changes a word in
    the middle retypes everything after it, which is what the user would
    see a human do and keeps the state impossible to get wrong."""
    keep = 0
    for a, b in zip(typed, target):
        if a != b:
            break
        keep += 1
    return len(typed) - keep, target[keep:]


class LiveTyper:
    """Types revisions at the cursor. Never raises: typing is a bonus
    tier, and a dictation that cannot type still lands on the clipboard."""

    def __init__(self, gi_python: str):
        self.typed = ""
        self.failed = False
        try:
            self.proc = subprocess.Popen(
                [gi_python, str(HELPER), "--stream"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True)
        except OSError:
            self.proc = None
            self.failed = True

    def _send(self, back: int, text: str) -> None:
        if self.failed or self.proc is None or self.proc.poll() is not None:
            self.failed = True
            return
        try:
            self.proc.stdin.write(
                json.dumps({"back": back, "text": text}) + "\n")
            self.proc.stdin.flush()
            self.proc.stdout.readline()   # one edit at a time, in order
        except (OSError, ValueError):
            self.failed = True

    def update(self, text: str) -> None:
        """Bring the cursor in line with *text*."""
        back, tail = edit_to(self.typed, text)
        if not back and not tail:
            return
        self._send(back, tail)
        if not self.failed:
            self.typed = text

    def finish(self, text: str) -> bool:
        """Type the final transcript. True when the cursor holds it, so
        the caller knows whether to fall back to the clipboard."""
        if not self.failed:
            # The hotkey that ended the recording may still be held, and
            # injecting keysyms under live modifiers desyncs the
            # compositor (see deliver._wait_for_chord).
            from .deliver import _wait_for_chord
            _wait_for_chord()
            self.update(text)
        ok = not self.failed and self.typed == text
        self.close()
        return ok

    def discard(self) -> None:
        """Erase everything typed so far: a cancelled dictation must not
        leave half a sentence in the document."""
        if not self.failed and self.typed:
            self._send(len(self.typed), "")
            self.typed = ""
        self.close()

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.proc.kill()
        self.proc = None


def available() -> bool:
    """Whether live typing can run at all: same gates as the portal tier
    in deliver, since it is the same session and the same grant."""
    from .deliver import _portal_enabled, _portal_token
    return (_portal_enabled() and _portal_token().exists()
            and HELPER.exists() and runstate.RUN is not None)
