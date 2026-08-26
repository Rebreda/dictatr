"""Always-on capture: VAD-segment the mic continuously, archive every
utterance. No typing, no clipboard, no notification churn — the archive
(and `dictatr gc`, which sweeps out the inevitable junk) is the product.

Runs foreground (`dictatr listen`, or the systemd unit in systemd/). While
a hotkey dictation session is live (runstate.DICTATE_PID) the listener
releases the microphone and discards any half-captured utterance — the
interactive session archives its own audio, so this avoids duplicate rows.

Always the client-VAD + batch path, never /realtime: each utterance is a
complete clip and batch transcription of complete clips is the reliable
path (see vad.py). If Lemonade is down the clip is still archived, with
meta.pending_transcription set; the next `dictatr listen` start (or
`dictatr gc`) retries those rather than losing audio.
"""

import asyncio
import contextlib
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from . import mic, runstate
from .batch import pcm_to_wav_bytes, transcribe_bytes
from .settings import settings
from .storage import patch_manifest_record, save_recording
from .vad import capture_utterance

POLL_S = 1.0          # dictate-pidfile poll while paused
RETRY_DELAYS = (5, 20)  # transcription retries before archiving untranscribed
MAX_INFLIGHT = 4      # transcriptions allowed behind the capture loop


def _log(msg: str) -> None:
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}",
          flush=True)


def batch_model() -> str:
    """ASR model for listen's batch transcriptions. Streaming models only
    work over /realtime (the batch endpoint returns "" for them, measured
    2026-08), so fall back to a batch-capable Whisper unless overridden."""
    if settings.listen.model:
        return settings.listen.model
    m = settings.whisper.model
    return "Whisper-Large-v3-Turbo" if "streaming" in m.lower() else m


def _transcribe_retry(pcm: bytes) -> str | None:
    """Transcribe, retrying briefly. None = Lemonade unreachable."""
    for delay in (0, *RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return transcribe_bytes(pcm_to_wav_bytes(pcm), model=batch_model())
        except OSError:
            continue
    return None


def retry_pending(limit: int = 50) -> int:
    """Re-transcribe rows archived while Lemonade was down, plus listen
    rows whose transcript came back empty (once — e.g. rows written before
    the streaming-model batch fallback existed). Returns rows completed."""
    base = Path(settings.storage.base).expanduser()
    manifest = base / "manifest.jsonl"
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    done = 0
    for line in lines:
        if done >= limit:
            break
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        meta = row.get("meta") or {}
        empty_listen = (meta.get("mode") == "listen"
                        and not (row.get("raw_transcription") or "").strip()
                        and not meta.get("retranscribed")
                        and not row.get("gc"))
        if not (meta.get("pending_transcription") or empty_listen):
            continue
        try:
            wav = Path(row["audio_path"]).read_bytes()
            text = transcribe_bytes(wav, model=batch_model())
        except OSError:
            break  # still unreachable; keep the flags for next time
        patch_manifest_record(manifest, row["uuid"], {
            "raw_transcription": text,
            "corrected_transcription": text,
            "whisper_model": batch_model(),
            "meta": {**meta, "pending_transcription": False,
                     "retranscribed": True},
        })
        done += 1
    return done


async def _archive(pcm: bytes, sem: asyncio.Semaphore) -> None:
    async with sem:
        text = await asyncio.to_thread(_transcribe_retry, pcm)
        meta = {"mode": "listen"}
        if text is None:
            meta["pending_transcription"] = True
        try:
            record = save_recording(
                pcm, text or "",
                storage_base=Path(settings.storage.base).expanduser(),
                whisper_model=batch_model(),
                meta=meta,
            )
        except (OSError, ValueError) as e:
            _log(f"archive failed: {e!r}")
            return
        secs = record["duration_s"]
        if text is None:
            _log(f"archived {secs}s clip untranscribed (Lemonade down)")
        else:
            _log(f"archived {secs}s: {text[:80] or '(no speech recognized)'}")
        if text and settings.listen.tag:
            from . import concepts
            with contextlib.suppress(Exception):  # best-effort, like cli
                await asyncio.to_thread(concepts.annotate, record)


async def _run() -> int:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)

    sem = asyncio.Semaphore(MAX_INFLIGHT)
    tasks: set[asyncio.Task] = set()
    n = await asyncio.to_thread(retry_pending)
    if n:
        _log(f"transcribed {n} clip(s) archived while Lemonade was down")
    _log("listening (always-on); pauses while a hotkey session is active")

    while not stopping.is_set():
        if runstate.live_pid(runstate.DICTATE_PID):
            await asyncio.sleep(POLL_S)
            continue

        # One mic stream per cycle; a cycle ends on shutdown or when a
        # hotkey session starts (watcher trips cycle_stop, mic is released).
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
            while not cycle_stop.is_set():
                utt = await capture_utterance(source, cycle_stop)
                if utt is None:
                    if settings.input_file:  # test stream exhausted
                        stopping.set()
                        cycle_stop.set()
                    continue  # mic: max_wait quiet spell; keep listening
                if runstate.live_pid(runstate.DICTATE_PID):
                    continue  # hotkey session owns this utterance
                task = asyncio.create_task(_archive(utt.pcm, sem))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            cycle_stop.set()
            watcher.cancel()
            await source.aclose()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
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
    import signal as _signal
    import subprocess

    from . import deliver as dlv

    if pid := runstate.live_pid(runstate.LISTEN_PID):
        os.kill(pid, _signal.SIGTERM)
        dlv.notify("Always-on capture: off", 2500)
        return 0
    runstate.RUN.mkdir(parents=True, exist_ok=True)
    with open(runstate.RUN / "listen.log", "ab") as log:
        subprocess.Popen([sys.executable, "-m", "dictatr.cli", "listen"],
                         start_new_session=True, stdout=log,
                         stderr=subprocess.STDOUT)
    dlv.notify("Always-on capture: on 🎙 (everything is archived)", 4000)
    return 0
