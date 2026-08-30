"""dictatr command-line interface.

Commands (hotkey-oriented, single instance coordinated via a pidfile):
  toggle [--clip]   start listening; if already listening, stop now (commit)
  cancel            abort the current listening session, no transcription
  file PATH         transcribe an audio file via the batch HTTP API
  setup             first-run wizard (engine, typing, hotkeys, test)
  backend ...       inference backend: status|start|stop|pull MODEL|models
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
from . import runstate
from .batch import transcribe_file
from . import livetype
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

    if not settings.input_file and mic.source_muted():
        dlv.notify("Microphone is muted — unmute to dictate", 6000,
                   category="errors")
        return 1
    source = (
        mic.file_chunks(settings.input_file, stop_now,
                        realtime=os.environ.get("DICTATE_INPUT_PACED") == "1")
        if settings.input_file
        else mic.mic_chunks(stop_now)
    )
    # Live typing: put words at the cursor as they are transcribed
    # instead of all at once at the end. Only for plain dictation --
    # ask mode's answer is what gets delivered, not the question.
    typer = None
    if (prefer_typing and not ask and settings.typing.live
            and livetype.available()):
        typer = livetype.LiveTyper(dlv.gi_python())

    def on_partial(running):
        if typer is not None:
            typer.update(running)

    try:
        # Server-side VAD (Moonshine + TEN-VAD) via /realtime, always.
        # The batch endpoint is only for `dictate file`.
        # A cold model loads before anything listens; say so, rather
        # than leaving the user talking into a gap.
        await asyncio.to_thread(
            ensure_asr_loaded,
            lambda m: dlv.notify(f"Loading {m}… (first time only)", 20000))
        text, pcm = await dictate_once(
            source, stop_now, on_state,
            on_partial=(on_partial if typer is not None else None))
    except (ConnectionError, OSError, RuntimeError) as e:
        if typer is not None:
            typer.discard()
        dlv.notify(f"Lemonade error: {e}", 6000, category="errors")
        return 1

    if cancelled:
        if typer is not None:
            typer.discard()
        dlv.notify("Cancelled", 1500)
        return 0
    if not text:
        if typer is not None:
            typer.discard()
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
            dlv.notify(f"LLM unreachable: {e}", 6000, category="errors")
            return 1
        PIDFILE.unlink(missing_ok=True)  # session over; free the hotkey
        if quiet:
            dlv.deliver(answer, prefer_typing)
        else:
            subprocess.run(["wl-copy"], input=answer.encode(), check=False)
            runstate.mark_done()
            dlv.notify(f"{answer[:400]}", 15000, category="answers")
            if settings.llm.speak:
                await asyncio.to_thread(llm.speak, answer)
    else:
        PIDFILE.unlink(missing_ok=True)  # session over; free the hotkey
        if typer is not None and typer.finish(text):
            runstate.mark_done()
            dlv.notify(f"Typed: {text[:120]}", category="delivery")
        else:
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
    # Tell the tray what this session does with the transcript.
    runstate.write_mode("ask" if ask else "type" if prefer_typing else "clip")
    try:
        return asyncio.run(_listen(prefer_typing, ask, quiet))
    finally:
        PIDFILE.unlink(missing_ok=True)
        runstate.MODE.unlink(missing_ok=True)


def cmd_cancel() -> int:
    if pid := _live_pid():
        os.kill(pid, signal.SIGTERM)
    return 0


def cmd_backend(args) -> int:
    import json
    import urllib.request
    from .backend import client, lemond

    if args.backend_cmd == "start":
        print(f"managed lemond up at {lemond.start()}")
        return 0
    if args.backend_cmd == "stop":
        print("stopped" if lemond.stop() else "not running")
        return 0

    b = client.resolve(allow_start=False)
    if args.backend_cmd == "status":
        try:
            loaded = b.health().get("all_models_loaded", [])
            server = f"up ({len(loaded)} model(s) loaded)"
        except Exception:
            server = "unreachable"
        st = lemond.status()
        print(f"provider: {b.kind}\n"
              f"api_base: {b.api_base}\n"
              f"server:   {server}\n"
              f"managed:  {'running' if st['running'] else 'stopped'}, "
              f"binary {st['binary'] or 'not installed'} "
              f"(pinned {st['version']})")
        return 0
    if args.backend_cmd == "models":
        req = urllib.request.Request(f"{b.api_base}/models",
                                     headers=b.headers())
        with urllib.request.urlopen(req, timeout=10) as r:
            for m in json.load(r).get("data", []):
                print(m["id"])
        return 0
    if args.backend_cmd == "pull":
        def progress(ev):
            pct = ev.get("percent")
            name = ev.get("file") or args.model
            print(f"\r{name}: {pct if pct is not None else '?'}%",
                  end="", flush=True)
        lemond.pull(args.model, on_progress=progress,
                    base=b.api_base, key=b.api_key)
        print(f"\r{args.model}: done      ")
        return 0
    return 2


def cmd_setup() -> int:
    """Run the first-run wizard. It needs PyGObject, so it goes through
    bin/dictate-setup, which picks a python that has it."""
    shim = Path(__file__).resolve().parents[2] / "bin" / "dictate-setup"
    if not shim.exists():
        print(f"setup wizard not found at {shim}", file=sys.stderr)
        return 1
    return subprocess.call([str(shim)])


def cmd_file(path: str) -> int:
    text = transcribe_file(path)
    if not text:
        dlv.notify(f"No speech detected in {path}")
        return 1
    dlv.deliver(text, prefer_typing=False)
    return 0


def _legacy_archive_note() -> None:
    """Said once per command, until the recordings are moved.

    Early versions archived into ~/.listenr/dictation, which is not
    dictatr's to write to. Moving is a one-time script rather than
    something the app does on its own: it is the user's data, tens of
    megabytes of it, and quietly relocating it while they are dictating
    is not an improvement worth making behind their back."""
    from .settings import legacy_archive_pending
    if not legacy_archive_pending():
        return
    tool = Path(__file__).resolve().parents[2] / "tools" / "archive-migrate"
    print(f"dictatr: recordings are still in ~/.listenr/dictation. "
          f"Move them with:\n    {tool} --go", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(prog="dictatr", description=__doc__)
    sub = p.add_subparsers(dest="cmd")
    t = sub.add_parser("toggle", help="start listening / stop current recording")
    t.add_argument("--clip", action="store_true",
                   help="deliver to clipboard even when typing is available")
    a = sub.add_parser("ask", help="speak a question, get an LLM answer (spoken + clipboard)")
    a.add_argument("--quiet", action="store_true",
                   help="no TTS or notification chatter; deliver the answer "
                        "like a dictation (typed at cursor, else clipboard)")
    sub.add_parser("cancel", help="abort without transcribing")
    sub.add_parser("setup", help="run the setup wizard (engine, typing, "
                                 "hotkeys, a test dictation)")
    sub.add_parser("tag", help="backfill concept tags for untagged archive rows")
    f = sub.add_parser("file", help="transcribe an audio file to the clipboard")
    f.add_argument("path")
    ls = sub.add_parser("listen", help="always-on: archive every utterance "
                                       "until stopped (pauses during hotkey "
                                       "sessions)")
    ls.add_argument("--toggle", action="store_true",
                    help="start detached, or stop the running listener")
    be = sub.add_parser("backend",
                        help="inference backend: status, lifecycle, models")
    besub = be.add_subparsers(dest="backend_cmd", required=True)
    besub.add_parser("status", help="active provider and server health")
    besub.add_parser("start", help="start the managed lemond instance")
    besub.add_parser("stop", help="stop the managed lemond instance")
    bp = besub.add_parser("pull", help="download a model onto the backend")
    bp.add_argument("model")
    besub.add_parser("models", help="list models on the active backend")
    g = sub.add_parser("gc", help="quarantine junk archive clips, purge old trash")
    g.add_argument("--dry-run", action="store_true",
                   help="report what would be quarantined, touch nothing")
    g.add_argument("--purge-days", type=float, default=None,
                   help="override gc_purge_days for this run (0 = purge all trash now)")
    g.add_argument("--restore", metavar="UID",
                   help="move a quarantined clip back into the archive")
    g.add_argument("--notify", action="store_true",
                   help="report the result in a notification, not stdout")
    args = p.parse_args()
    _legacy_archive_note()

    if args.cmd in (None, "toggle"):
        clip = getattr(args, "clip", False)
        sys.exit(cmd_toggle(prefer_typing=not clip))
    if args.cmd == "ask":
        quiet = getattr(args, "quiet", False)
        sys.exit(cmd_toggle(prefer_typing=quiet, ask=True, quiet=quiet))
    if args.cmd == "cancel":
        sys.exit(cmd_cancel())
    if args.cmd == "setup":
        sys.exit(cmd_setup())
    if args.cmd == "tag":
        n = concepts.sweep(limit=200)
        print(f"tagged {n} recordings")
        sys.exit(0)
    if args.cmd == "file":
        sys.exit(cmd_file(args.path))
    if args.cmd == "backend":
        sys.exit(cmd_backend(args))
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
        line = (f"kept {summary['kept']}, quarantined "
                f"{sum(q.values())} ({q or 'none'}), "
                f"purged {summary['purged']}")
        # --notify is for the surfaces: a sweep started from a menu has
        # no terminal to print to, and the alternative was each of them
        # spawning a shell to run this and then notify-send.
        if args.notify:
            dlv.notify(line, category="toggles")
        else:
            print(line)
        sys.exit(0)


if __name__ == "__main__":
    main()
