"""Keep the conversations, not just the questions.

Ask mode archived the audio of a spoken question and nothing else: the
answer, anything typed, every action run on a message and any screenshot
asked about were gone the moment the card closed. A conversation is the
part worth keeping, so each one is written as it happens.

    <archive>/chats/YYYY-MM-DD/chat_<uid>.jsonl   one JSON turn per line
    <archive>/chats/YYYY-MM-DD/chat_<uid>-N.png   assets asked about

Append-only, one line per turn, in the archive's own vocabulary
(timestamp, app, text), so listenr tooling reading the archive finds
chats the same way it finds dictations. Recording is best-effort: a
conversation must never fail because a log line could not be written.
"""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import runstate
from .settings import settings


def _base() -> Path | None:
    if not settings.storage.enabled:
        return None
    return Path(settings.storage.base).expanduser()


class ChatLog:
    """One conversation. Created on the first turn, so a card that is
    opened and dismissed leaves nothing behind."""

    def __init__(self):
        self.path = None
        self.uid = uuid.uuid4().hex[:12]
        self._assets = 0

    def _open(self) -> Path | None:
        if self.path is not None:
            return self.path
        base = _base()
        if base is None:
            return None
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        folder = base / "chats" / day
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        self.path = folder / f"chat_{self.uid}.jsonl"
        return self.path

    def turn(self, role: str, text: str, **extra) -> None:
        """Record one turn: role is user, assistant or action."""
        path = self._open()
        if path is None or not (text or "").strip():
            return
        row = {"timestamp": datetime.now(timezone.utc).isoformat(),
               "role": role, "text": text, "app": runstate.read_app(),
               **{k: v for k, v in extra.items() if v is not None}}
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def asset(self, src: str) -> str | None:
        """Copy something the conversation is about (a screenshot lives
        in the runtime dir and is swept within the hour) into the
        archive, and return where it landed."""
        path = self._open()
        if path is None:
            return None
        self._assets += 1
        dest = path.with_name(f"{path.stem}-{self._assets}{Path(src).suffix}")
        try:
            shutil.copy2(src, dest)
        except OSError:
            return None
        return str(dest)
