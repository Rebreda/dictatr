"""Single-utterance dictation over Lemonade's /realtime WebSocket.

Speaks the same protocol as listenr's LemonadeUnifiedASR.stream_transcribe
(session.update with turn_detection → input_audio_buffer.append → server-side
VAD events → conversation.item.input_audio_transcription.completed), reduced
to the dictation case: capture exactly one utterance and return its text.

The server does the VAD. This module has no energy thresholds of its own.
"""

import asyncio
import base64
import json
import logging
import urllib.request

import websockets

from .settings import settings

log = logging.getLogger("dictatr.engine")

COMPLETED = "conversation.item.input_audio_transcription.completed"
SPEECH_STARTED = "input_audio_buffer.speech_started"
SPEECH_STOPPED = "input_audio_buffer.speech_stopped"


def realtime_ws_url() -> str:
    """Prefer the /realtime proxy on the main API port; fall back to the
    dedicated websocket port advertised by /v1/health (listenr's approach)."""
    base = settings.whisper.api_base  # e.g. http://localhost:8080/api/v1
    proxied = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{proxied}/realtime?model={settings.whisper.model}"


def health_ws_url() -> str:
    root = settings.whisper.api_base.split("/api/")[0]
    try:
        with urllib.request.urlopen(f"{root}/v1/health", timeout=5) as r:
            port = json.load(r).get("websocket_port", 8001)
    except Exception:
        port = 8001
    return f"ws://localhost:{port}/realtime?model={settings.whisper.model}"


async def dictate_once(
    audio_stream,
    stop_now: asyncio.Event,
    on_state=lambda state: None,
) -> tuple[str | None, bytes]:
    """Stream *audio_stream* until the server transcribes one utterance.

    stop_now: set externally (hotkey) to force a commit immediately.
    on_state: callback for UI feedback: "listening" | "speech" | "transcribing".
    Returns (text or None, captured_pcm_bytes).
    """
    vad = settings.vad
    session_update = {
        "type": "session.update",
        "session": {
            "model": settings.whisper.model,
            "turn_detection": {
                "threshold": vad.threshold,
                "silence_duration_ms": vad.silence_duration_ms,
                "prefix_padding_ms": vad.prefix_padding_ms,
            },
        },
    }

    urls = [realtime_ws_url(), health_ws_url()]
    last_err = None
    for url in urls:
        try:
            async with websockets.connect(url, max_size=10 * 1024 * 1024) as ws:
                return await _run_session(ws, session_update, audio_stream,
                                          stop_now, on_state)
        except (OSError, websockets.exceptions.InvalidStatus) as e:
            last_err = e
            log.warning("realtime connect failed for %s: %s", url, e)
    raise ConnectionError(f"no Lemonade /realtime endpoint reachable: {last_err}")


async def _run_session(ws, session_update, audio_stream, stop_now, on_state):
    await ws.send(json.dumps(session_update))
    on_state("listening")

    captured = bytearray()
    speech_seen = asyncio.Event()
    result: dict = {}
    done = asyncio.Event()

    async def send_audio():
        sent_s = 0.0
        async for chunk in audio_stream:
            captured.extend(chunk)
            sent_s += len(chunk) / 2 / 16000
            await ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode(),
            }))
            if stop_now.is_set():
                break
            if speech_seen.is_set() and sent_s >= settings.vad.max_segment_s:
                break
        # Mic ended or stop requested: force transcription of what remains.
        try:
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def recv_events():
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type", "")
            if t == SPEECH_STARTED:
                speech_seen.set()
                on_state("speech")
            elif t == SPEECH_STOPPED:
                on_state("transcribing")
            elif t == COMPLETED:
                result["text"] = (msg.get("transcript") or "").strip()
                done.set()
                return
            elif t == "error":
                result["error"] = msg.get("error")
                done.set()
                return

    sender = asyncio.ensure_future(send_audio())
    receiver = asyncio.ensure_future(recv_events())
    try:
        # No speech at all within max_wait → give up quietly.
        try:
            await asyncio.wait_for(speech_seen.wait(), settings.vad.max_wait_s)
        except asyncio.TimeoutError:
            stop_now.set()
            return None, bytes(captured)
        # Speech happened: wait for the transcript (VAD commits on silence,
        # send_audio commits on stop/max-segment).
        await asyncio.wait_for(done.wait(), settings.vad.max_segment_s + 30)
    except asyncio.TimeoutError:
        return None, bytes(captured)
    finally:
        stop_now.set()  # stops the mic generator
        for t in (sender, receiver):
            t.cancel()
        for t in (sender, receiver):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    if "error" in result:
        raise RuntimeError(f"Lemonade realtime error: {result['error']}")
    text = result.get("text") or None
    if text in (".", "[BLANK_AUDIO]"):
        text = None
    return text, bytes(captured)
