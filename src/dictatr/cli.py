"""dictatr command-line interface.

Commands (hotkey-oriented, single instance coordinated via a pidfile):
  toggle [--clip]   start listening; if already listening, stop now (commit)
  cancel            abort the current listening session, no transcription
  file PATH         transcribe an audio file via the batch HTTP API
"""

import asyncio
import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from . import concepts
from . import deliver as dlv
from . import llm
from . import recall
from . import mic
from .batch import transcribe_file
from .engine import dictate_once, ensure_asr_loaded
from .runstate import DICTATE_PID as PIDFILE, live_pid, write_pid
from .settings import settings
from .storage import save_recording


def _live_pid() -> int | None:
    return live_pid(PIDFILE)


async def _listen(prefer_typing: bool, ask: bool = False,
                  quiet: bool = False) -> int:
    stop_now = asyncio.Event()
    cancelled = False

    def on_stop(*_):  # hotkey pressed again -> commit and finish
        stop_now.set()

    def on_cancel(*_):
        nonlocal cancelled
        cancelled = True
        stop_now.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGUSR1, on_stop)
    loop.add_signal_handler(signal.SIGTERM, on_cancel)

    states = {
        "listening": "🎙 Listening… speak (hotkey again = stop now)",
        "speech": None,  # no notification churn mid-speech
        "transcribing": "Transcribing…",
    }

    def on_state(state):
        text = states.get(state)
        if text:
            dlv.notify(text, 15000)

    source = (
        mic.file_chunks(settings.input_file, stop_now,
                        realtime=os.environ.get("DICTATE_INPUT_PACED") == "1")
        if settings.input_file
        else mic.mic_chunks(stop_now)
    )
    try:
        # Server-side VAD (Moonshine + TEN-VAD) via /realtime, always.
        # The batch endpoint is only for `dictate file`.
        await asyncio.to_thread(ensure_asr_loaded)
        text, pcm = await dictate_once(source, stop_now, on_state)
    except (ConnectionError, OSError, RuntimeError) as e:
        dlv.notify(f"Lemonade error: {e}", 6000)
        return 1

    if cancelled:
        dlv.notify("Cancelled", 1500)
        return 0
    if not text:
        dlv.notify("No speech detected")
        return 0

    if ask:
        dlv.notify(f"You: {text}", 3000)
        context = []
        if settings.llm.recall and settings.storage.enabled:
            try:
                context = await asyncio.to_thread(recall.search, text)
            except OSError:
                pass  # no embedding model available; answer without recall
        try:
            answer = await asyncio.to_thread(llm.chat, text, context)
        except OSError as e:
            dlv.notify(f"LLM unreachable: {e}", 6000)
            return 1
        PIDFILE.unlink(missing_ok=True)  # session over; free the hotkey
        if quiet:
            dlv.deliver(answer, prefer_typing)
        else:
            subprocess.run(["wl-copy"], input=answer.encode(), check=False)
            dlv.notify(f"{answer[:400]}", 15000)
            if settings.llm.speak:
                await asyncio.to_thread(llm.speak, answer)
    else:
        PIDFILE.unlink(missing_ok=True)  # session over; free the hotkey
        dlv.deliver(text, prefer_typing)
    if settings.storage.enabled and pcm:
        record = save_recording(
            pcm, text,
            storage_base=Path(settings.storage.base).expanduser(),
            whisper_model=settings.whisper.model,
            meta={"mode": "ask" if ask else "dictate"},
        )
        if settings.llm.concepts:
            try:
                await asyncio.to_thread(concepts.annotate, record)
                if ask:  # LLM is warm anyway: backfill older rows too
                    await asyncio.to_thread(concepts.sweep, 10)
            except Exception as e:  # best-effort, never blocks the user
                print(f"concept tagging failed: {e!r}", file=sys.stderr)
    return 0


def cmd_toggle(prefer_typing: bool, ask: bool = False,
               quiet: bool = False) -> int:
    if pid := _live_pid():
        os.kill(pid, signal.SIGUSR1)
        return 0
    write_pid(PIDFILE)
    try:
        return asyncio.run(_listen(prefer_typing, ask, quiet))
    finally:
        PIDFILE.unlink(missing_ok=True)


def cmd_cancel() -> int:
    if pid := _live_pid():
        os.kill(pid, signal.SIGTERM)
    return 0


def cmd_file(path: str) -> int:
    text = transcribe_file(path)
    if not text:
        dlv.notify(f"No speech detected in {path}")
        return 1
    dlv.deliver(text, prefer_typing=False)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="dictatr", description=__doc__)
    sub = p.add_subparsers(dest="cmd")
    t = sub.add_parser("toggle", help="start listening / stop current recording")
    t.add_argument("--clip", action="store_true",
                   help="deliver to clipboard even if ydotool is available")
    a = sub.add_parser("ask", help="speak a question, get an LLM answer (spoken + clipboard)")
    a.add_argument("--quiet", action="store_true",
                   help="no TTS or notification chatter; deliver the answer "
                        "like a dictation (typed at cursor, else clipboard)")
    sub.add_parser("cancel", help="abort without transcribing")
    sub.add_parser("tag", help="backfill concept tags for untagged archive rows")
    f = sub.add_parser("file", help="transcribe an audio file to the clipboard")
    f.add_argument("path")
    ls = sub.add_parser("listen", help="always-on: archive every utterance "
                                       "until stopped (pauses during hotkey "
                                       "sessions)")
    ls.add_argument("--toggle", action="store_true",
                    help="start detached, or stop the running listener")
    g = sub.add_parser("gc", help="quarantine junk archive clips, purge old trash")
    g.add_argument("--dry-run", action="store_true",
                   help="report what would be quarantined, touch nothing")
    g.add_argument("--purge-days", type=float, default=None,
                   help="override gc_purge_days for this run (0 = purge all trash now)")
    g.add_argument("--restore", metavar="UID",
                   help="move a quarantined clip back into the archive")
    args = p.parse_args()

    if args.cmd in (None, "toggle"):
        clip = getattr(args, "clip", False)
        sys.exit(cmd_toggle(prefer_typing=not clip))
    if args.cmd == "ask":
        quiet = getattr(args, "quiet", False)
        sys.exit(cmd_toggle(prefer_typing=quiet, ask=True, quiet=quiet))
    if args.cmd == "cancel":
        sys.exit(cmd_cancel())
    if args.cmd == "tag":
        n = concepts.sweep(limit=200)
        print(f"tagged {n} recordings")
        sys.exit(0)
    if args.cmd == "file":
        sys.exit(cmd_file(args.path))
    if args.cmd == "listen":
        from . import listen
        sys.exit(listen.toggle() if args.toggle else listen.main())
    if args.cmd == "gc":
        from . import cleanup
        if args.restore:
            sys.exit(0 if cleanup.restore(args.restore) else 1)
        summary = cleanup.sweep(dry_run=args.dry_run,
                                purge_days=args.purge_days)
        q = summary["quarantined"]
        print(f"kept {summary['kept']}, quarantined "
              f"{sum(q.values())} ({q or 'none'}), purged {summary['purged']}")
        sys.exit(0)


if __name__ == "__main__":
    main()
