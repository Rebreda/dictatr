"""Local tools the ask-mode LLM can call, and a persistent memory store.

Everything is read-only against the system except `remember`, which appends
to a plain JSONL file in the archive. Commands run as argv lists (no shell),
with timeouts and hard output caps. The registry is built dynamically so
tools whose backing binary is missing (e.g. a calendar app) simply don't
exist as far as the model knows.
"""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .settings import settings


def memories_path() -> Path:
    return Path(settings.storage.base).expanduser() / "memories.jsonl"


def load_memories(limit: int = 30) -> list[str]:
    try:
        lines = memories_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    facts = []
    for line in lines[-limit:]:
        try:
            facts.append(json.loads(line)["fact"])
        except (json.JSONDecodeError, KeyError):
            continue
    return facts


def _run(argv: list[str], timeout: float = 6.0) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=timeout).stdout.strip()
        return out[:2000] or "(no output)"
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(tool failed: {e})"


def t_current_time() -> str:
    return _run(["date", "+%A %Y-%m-%d %H:%M:%S %Z"])


def t_find_files(pattern: str) -> str:
    pattern = pattern.strip().strip("'\"")
    if not pattern or len(pattern) > 80:
        return "(invalid pattern)"
    argv = ["find", str(Path.home()), "-maxdepth", "5",
            "-not", "-path", "*/.*", "-iname", f"*{pattern}*"]
    out = _run(argv, timeout=8.0)
    lines = out.splitlines()
    if len(lines) > 20:
        return "\n".join(lines[:20]) + f"\n(... {len(lines) - 20} more)"
    return out


def t_remember(fact: str) -> str:
    fact = fact.strip()
    if not fact:
        return "(nothing to remember)"
    path = memories_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fact": fact,
        }, ensure_ascii=False) + "\n")
    return f"Remembered: {fact}"


def t_calendar() -> str:
    if shutil.which("khal"):
        return _run(["khal", "list", "today", "7d"])
    if shutil.which("calcurse"):
        return _run(["calcurse", "-a", "-r7"])
    return _run(["cal", "-3"])  # at least show the months


def _schema(name, description, params=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object",
                       "properties": params or {},
                       "required": list(params or {})}}}


def registry():
    """(schemas, executors) for the tools available on this machine."""
    tools = {
        "current_time": (
            _schema("current_time", "Current local date, time and weekday"),
            lambda args: t_current_time()),
        "find_files": (
            _schema("find_files",
                    "Search the user's home directory for files whose name "
                    "contains a pattern",
                    {"pattern": {"type": "string",
                                 "description": "substring of the filename"}}),
            lambda args: t_find_files(args.get("pattern", ""))),
        "remember": (
            _schema("remember",
                    "Store a lasting fact the user states about themselves, "
                    "their preferences, or their work, for future sessions",
                    {"fact": {"type": "string",
                              "description": "the fact, one sentence"}}),
            lambda args: t_remember(args.get("fact", ""))),
        "calendar": (
            _schema("calendar", "The user's local calendar for the next days"),
            lambda args: t_calendar()),
    }
    schemas = [s for s, _ in tools.values()]
    executors = {name: fn for name, (_, fn) in tools.items()}
    return schemas, executors
