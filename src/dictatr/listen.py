"""Always-on capture over Lemonade's /realtime session (server-side VAD).

One persistent websocket, Moonshine's bundled TEN-VAD segmenting speech
server-side, transcripts arriving live — the same engine the hotkey path
uses, running forever. Every transcribed segment is archived; segments
the server hears as nothing are dropped, never archived. No typing, no
clipboard, no notification churn — the archive (and `dictatr gc`) is the
product.

Runs foreground (`dictatr listen`, or the systemd unit in systemd/). While
a hotkey dictation session is live (runstate.DICTATE_PID) the listener
releases the microphone and its session — the interactive session archives
its own audio, so this avoids duplicate rows. On startup the ASR model is
pinned in Lemonade (`lemonade load --pinned`) so other models can't evict
it mid-day; if Lemonade goes down the listener just reconnects with
backoff — capture depends on the server, by design.
"""

import asyncio
import contextlib
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import mic, runstate
from .engine import ensure_asr_loaded, stream_utterances
from .settings import settings
from .storage import save_recording

POLL_S = 1.0                     # dictate-pidfile poll while paused
BACKOFF_S = (5, 15, 30, 60)      # reconnect ladder while Lemonade is down


def _log(msg: str) -> None:
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}",
          flush=True)


def pin_model() -> None:
    """Keep the ASR model resident: pinned via the lemonade CLI when
    available, else the batch-warmup fallback. Eviction by another model
    was a real always-on failure mode."""
    if shutil.which("lemonade"):
        try:
            subprocess.run(
                ["lemonade", "load", settings.whisper.model, "--pinned"],
                capture_output=True, timeout=300)
            _log(f"pinned {settings.whisper.model} in Lemonade")
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    ensure_asr_loaded()


def archive(text: str, pcm: bytes) -> dict | None:
    if not (settings.storage.enabled and pcm and text):
        return None
    try:
        record = save_recording(
            pcm, text,
            storage_base=Path(settings.storage.base).expanduser(),
            whisper_model=settings.whisper.model,
            meta={"mode": "listen"},
        )
    except (OSError, ValueError) as e:
        _log(f"archive failed: {e!r}")
        return None
    _log(f"archived {record['duration_s']}s: {text[:80]}")
    return record


async def _run() -> int:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)

    await asyncio.to_thread(pin_model)
    _log("listening (always-on, server VAD); pauses while a hotkey "
         "session is active")
    failures = 0

    while not stopping.is_set():
        if runstate.live_pid(runstate.DICTATE_PID):
            await asyncio.sleep(POLL_S)
            continue

        cycle_stop = asyncio.Event()

        async def watch(cycle_stop=cycle_stop):
            while not cycle_stop.is_set():
                if stopping.is_set() or runstate.live_pid(runstate.DICTATE_PID):
                    cycle_stop.set()
                    return
                await asyncio.sleep(POLL_S)

        watcher = asyncio.create_task(watch())
        source = (mic.file_chunks(settings.input_file, cycle_stop)
                  if settings.input_file else mic.mic_chunks(cycle_stop))
        try:
            async for text, pcm in stream_utterances(source, cycle_stop):
                failures = 0
                if runstate.live_pid(runstate.DICTATE_PID):
                    continue  # hotkey session owns this utterance
                task = asyncio.to_thread(archive, text, pcm)
                if settings.listen.tag:
                    record = await task
                    if record:
                        from . import concepts
                        with contextlib.suppress(Exception):
                            await asyncio.to_thread(concepts.annotate, record)
                else:
                    await task
        except ConnectionError as e:
            failures += 1
            wait = BACKOFF_S[min(failures, len(BACKOFF_S)) - 1]
            _log(f"Lemonade unreachable ({e}); retrying in {wait}s")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), wait)
        except RuntimeError as e:
            _log(f"realtime session error: {e}; reconnecting")
        finally:
            cycle_stop.set()
            watcher.cancel()
            await source.aclose()
        if settings.input_file:
            stopping.set()  # test stream is one-shot

    _log("stopped")
    return 0


def main() -> int:
    if pid := runstate.live_pid(runstate.LISTEN_PID):
        print(f"already listening (pid {pid})", file=sys.stderr)
        return 1
    runstate.write_pid(runstate.LISTEN_PID)
    try:
        return asyncio.run(_run())
    finally:
        runstate.LISTEN_PID.unlink(missing_ok=True)


def toggle() -> int:
    """Hotkey/menu entry point: flip the always-on listener on or off.

    Off just SIGTERMs whatever runs it — a clean exit, so a systemd unit
    (Restart=on-failure) stays down too. On spawns a detached listener
    logging to $XDG_RUNTIME_DIR/dictatr/listen.log."""
    import os

    from . import deliver as dlv

    if pid := runstate.live_pid(runstate.LISTEN_PID):
        os.kill(pid, signal.SIGTERM)
        dlv.notify("Always-on capture: off", 2500)
        return 0
    runstate.RUN.mkdir(parents=True, exist_ok=True)
    with open(runstate.RUN / "listen.log", "ab") as log:
        subprocess.Popen([sys.executable, "-m", "dictatr.cli", "listen"],
                         start_new_session=True, stdout=log,
                         stderr=subprocess.STDOUT)
    dlv.notify("Always-on capture: on 🎙 (everything is archived)", 4000)
    return 0
