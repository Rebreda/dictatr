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


def _is_delta(msg_type: str) -> bool:
    # Word-level streaming deltas mid-segment (OpenAI realtime shape).
    return msg_type.endswith("transcription.delta")


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


def ensure_asr_loaded() -> None:
    """The /realtime endpoint doesn't auto-load models (the batch endpoint
    does), and other models can evict the ASR model from Lemonade's memory.
    Pre-flight: if it isn't loaded, a tiny batch request loads it."""
    root = settings.whisper.api_base.split("/api/")[0]
    try:
        with urllib.request.urlopen(f"{root}/v1/health", timeout=5) as r:
            loaded = {m.get("model_name")
                      for m in json.load(r).get("all_models_loaded", [])}
        if settings.whisper.model in loaded:
            return
    except Exception:
        return  # no health endpoint; let the session try its luck
    from .batch import pcm_to_wav_bytes, transcribe_bytes
    try:
        log.info("loading %s via batch warmup", settings.whisper.model)
        transcribe_bytes(pcm_to_wav_bytes(b"\x00" * 3200))
    except Exception as e:
        log.warning("ASR warmup failed: %s", e)


def _session_update() -> dict:
    vad = settings.vad
    return {
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


async def stream_utterances(audio_stream, stop: asyncio.Event,
                            on_state=lambda s: None):
    """Yield (text, pcm) per server-VAD segment, indefinitely, over one
    /realtime session — the always-on flavor of dictate_once. Segments the
    server transcribes to nothing are dropped, not yielded. Ends when
    *stop* is set or the connection dies (websockets.ConnectionClosed
    ends the generator; caller owns the reconnect policy). Raises
    ConnectionError when no endpoint accepts the connection."""
    last_err = None
    for url in (realtime_ws_url(), health_ws_url()):
        try:
            conn = await websockets.connect(url, max_size=10 * 1024 * 1024)
        except (OSError, websockets.exceptions.InvalidHandshake) as e:
            last_err = e
            log.warning("realtime connect failed for %s: %s", url, e)
            continue
        async with conn as ws:
            async for item in _segment_stream(ws, audio_stream, stop,
                                              on_state):
                yield item
        return
    raise ConnectionError(f"no Lemonade /realtime endpoint reachable: {last_err}")


async def _segment_stream(ws, audio_stream, stop, on_state):
    await ws.send(json.dumps(_session_update()))
    on_state("listening")

    prefix = 32000 * settings.vad.prefix_padding_ms // 1000  # bytes
    max_seg = int(32000 * settings.vad.max_segment_s)
    buf = bytearray()
    base = 0            # absolute stream offset of buf[0]
    mark = {"at": None}  # absolute offset where the live segment starts
    out: asyncio.Queue = asyncio.Queue()
    END = object()

    def trim(keep_from: int):
        nonlocal base
        drop = keep_from - base
        if drop > 0:
            del buf[:drop]
            base = keep_from

    async def send_audio():
        try:
            async for chunk in audio_stream:
                buf.extend(chunk)
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode(),
                }))
                if stop.is_set():
                    break
                if mark["at"] is None:
                    trim(base + max(0, len(buf) - 2 * prefix))
                elif base + len(buf) - mark["at"] > max_seg:
                    trim(mark["at"] + max_seg // 2)  # runaway-segment guard
                    mark["at"] = base
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await out.put(END)

    async def recv_events():
        try:
            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type", "")
                if t == SPEECH_STARTED:
                    mark["at"] = base + max(0, len(buf) - prefix)
                    on_state("speech")
                elif t == SPEECH_STOPPED:
                    on_state("transcribing")
                elif t == COMPLETED:
                    text = (msg.get("transcript") or "").strip()
                    start = mark["at"] if mark["at"] is not None else base
                    pcm = bytes(buf[start - base:])
                    mark["at"] = None
                    trim(base + len(buf))
                    if text and text not in (".", "[BLANK_AUDIO]"):
                        await out.put((text, pcm))
                    on_state("listening")
                elif t == "error":
                    await out.put(RuntimeError(
                        f"Lemonade realtime error: {msg.get('error')}"))
                    return
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await out.put(END)

    sender = asyncio.ensure_future(send_audio())
    receiver = asyncio.ensure_future(recv_events())
    try:
        while True:
            item = await out.get()
            if item is END:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        for t in (sender, receiver):
            t.cancel()
        for t in (sender, receiver):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def dictate_once(
    audio_stream,
    stop_now: asyncio.Event,
    on_state=lambda state: None,
    on_partial=None,
) -> tuple[str | None, bytes]:
    """Stream *audio_stream* until the server transcribes one utterance.

    stop_now: set externally (hotkey) to force a commit immediately.
    on_state: callback for UI feedback: "listening" | "speech" | "transcribing".
    on_partial: called with the running transcript (finished segments plus
    the in-flight segment's streaming deltas) whenever it grows — the live
    text a chat UI displays while the user is still talking.
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
                                          stop_now, on_state, on_partial)
        except (OSError, websockets.exceptions.InvalidHandshake) as e:
            last_err = e
            log.warning("realtime connect failed for %s: %s", url, e)
    raise ConnectionError(f"no Lemonade /realtime endpoint reachable: {last_err}")


async def _run_session(ws, session_update, audio_stream, stop_now, on_state,
                       on_partial=None):
    """Multi-segment dictation: the server's VAD ends a *segment* at every
    decent pause, so treating the first transcript as the whole dictation
    cuts speakers off mid-thought. Instead transcripts accumulate; the
    dictation ends when the hotkey fires, or when idle_s passes after the
    last transcript with no new speech."""
    await ws.send(json.dumps(session_update))
    on_state("listening")

    loop = asyncio.get_running_loop()
    captured = bytearray()
    speech_seen = asyncio.Event()
    transcripts: list[str] = []
    state = {"speech_active": False, "last_done": None, "error": None,
             "mic_ended": False}

    async def send_audio():
        sent_s = 0.0
        try:
            async for chunk in audio_stream:
                captured.extend(chunk)
                sent_s += len(chunk) / 2 / 16000
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode(),
                }))
                if stop_now.is_set():
                    break
                if sent_s >= settings.vad.max_segment_s:
                    break
            # Mic ended or stop requested: force out what remains.
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            state["mic_ended"] = True

    partial = [""]

    def emit_partial():
        if on_partial is not None:
            live = " ".join([*transcripts, partial[0]]).strip()
            if live:
                on_partial(live)

    async def recv_events():
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type", "")
            if t == SPEECH_STARTED:
                speech_seen.set()
                state["speech_active"] = True
                on_state("speech")
            elif t == SPEECH_STOPPED:
                state["speech_active"] = False
                on_state("transcribing")
            elif _is_delta(t):
                # Lemonade's deltas are cumulative: each event carries the
                # whole in-flight segment so far (observed 2026-08), so
                # replace — appending doubles the text until it settles.
                partial[0] = (msg.get("delta") or msg.get("transcript")
                              or partial[0])
                emit_partial()
            elif t == COMPLETED:
                text = (msg.get("transcript") or "").strip()
                if text and text not in (".", "[BLANK_AUDIO]"):
                    transcripts.append(text)
                partial[0] = ""
                emit_partial()
                state["speech_active"] = False
                state["last_done"] = loop.time()
                on_state("listening" if not stop_now.is_set() else "transcribing")
            elif t == "error":
                state["error"] = msg.get("error")
                return

    sender = asyncio.ensure_future(send_audio())
    receiver = asyncio.ensure_future(recv_events())
    try:
        # No speech at all within max_wait → give up quietly.
        try:
            await asyncio.wait_for(speech_seen.wait(), settings.vad.max_wait_s)
        except asyncio.TimeoutError:
            return None, bytes(captured)

        deadline_after_stop = None
        while state["error"] is None:
            await asyncio.sleep(0.1)
            now = loop.time()
            if stop_now.is_set() or state["mic_ended"]:
                # Hotkey/mic end: give the final commit a moment to land —
                # a transcript may still be in flight for the tail audio.
                if deadline_after_stop is None:
                    deadline_after_stop = now + 8.0
                settled = (state["last_done"] is not None
                           and not state["speech_active"]
                           and now - state["last_done"] >= 1.2)
                if settled or now >= deadline_after_stop:
                    break
            elif (transcripts and not state["speech_active"]
                    and state["last_done"] is not None
                    and now - state["last_done"] >= settings.vad.idle_s):
                break  # spoke, finished, and stayed quiet: dictation over
    finally:
        stop_now.set()  # stops the mic generator
        for t in (sender, receiver):
            t.cancel()
        for t in (sender, receiver):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    if state["error"] is not None:
        raise RuntimeError(f"Lemonade realtime error: {state['error']}")
    return (" ".join(transcripts) or None), bytes(captured)
