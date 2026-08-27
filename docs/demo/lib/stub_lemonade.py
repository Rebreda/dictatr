"""Deterministic Lemonade stand-in for demo captures.

Serves just enough of Lemonade's API for dictatr, with scripted results so
every take is identical — no models, no GPU, no transcription variance:

  GET  /v1/health, /api/v1/health   model list + websocket_port
  GET  /api/v1/models               model roster (feeds the settings UI)
  POST /api/v1/audio/transcriptions scenario["file_text"]
  POST /api/v1/chat/completions     scenario["chat_answer"] (after a delay)
  POST /api/v1/audio/speech         bytes of scenario["tts_wav"]
  WS   :ws-port/realtime            energy-VAD over the streamed PCM;
                                    transcripts come from
                                    scenario["transcripts"] in order

The websocket side runs real voice-activity detection on the audio dictatr
streams (RMS threshold + the session's silence_duration_ms), so notification
timing tracks the demo WAV naturally, while the *text* is scripted. VAD
events are appended to the cues file for camera synchronization.

Run with the repo venv (needs `websockets`).
"""

import argparse
import asyncio
import base64
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import websockets

MODELS = [
    {"id": "Moonshine-Medium-Streaming", "labels": ["transcription"]},
    {"id": "Whisper-Large-v3-Turbo", "labels": ["transcription"]},
    {"id": "Whisper-Base", "labels": ["transcription"]},
    {"id": "Qwen3.5-4B-GGUF", "labels": []},
    {"id": "gpt-oss-20b-mxfp4-GGUF", "labels": []},
    {"id": "kokoro-v1", "labels": []},
    {"id": "nomic-embed-text-v1-GGUF", "labels": []},
]

CHUNK_MS = 30  # dictatr streams 30 ms PCM16 chunks


def rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if not n:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[:n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5 / 32768.0


class Cues:
    def __init__(self, path: str):
        self.path = path

    def emit(self, event: str, **kw):
        with open(self.path, "a") as f:
            f.write(json.dumps({"t": time.time(), "event": event, **kw})
                    + "\n")


def http_handler(scenario: dict, ws_port: int):
    health = {
        "all_models_loaded": [{"model_name": m["id"]} for m in MODELS],
        "websocket_port": ws_port,
    }

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.1 so a stray websocket handshake against this port gets
        # a parseable rejection (client then falls back to the WS port).
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/v1/health", "/api/v1/health"):
                self._json(health)
            elif path == "/api/v1/models":
                self._json({"data": MODELS})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = self.path.split("?")[0]
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if path == "/api/v1/audio/transcriptions":
                self._json({"text": scenario.get("file_text", "")})
            elif path == "/api/v1/chat/completions":
                time.sleep(scenario.get("chat_delay_s", 1.0))
                self._json({"choices": [{"message": {
                    "role": "assistant",
                    "content": scenario.get("chat_answer", ""),
                }}]})
            elif path == "/api/v1/audio/speech":
                audio = Path(scenario["tts_wav"]).read_bytes() \
                    if scenario.get("tts_wav") else b""
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
            else:
                self._json({"error": "not found"}, 404)

    return Handler


async def realtime(ws, scenario: dict, cues: Cues):
    """One /realtime session: VAD on streamed PCM, scripted transcripts."""
    transcripts = list(scenario.get("transcripts", []))
    delay = scenario.get("transcribe_delay_ms", 400) / 1000
    threshold = 0.02
    silence_ms = 1200
    speech = False
    speech_ms = 0
    quiet_ms = 0
    seen_audio = False

    async def complete():
        nonlocal speech, quiet_ms, speech_ms
        speech = False
        speech_ms = 0
        await ws.send(json.dumps(
            {"type": "input_audio_buffer.speech_stopped"}))
        cues.emit("speech_stopped")
        await asyncio.sleep(delay)
        text = transcripts.pop(0) if transcripts else ""
        await ws.send(json.dumps({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": text,
        }))
        cues.emit("completed", text=text)

    cues.emit("session_open")
    async for raw in ws:
        msg = json.loads(raw)
        t = msg.get("type", "")
        if t == "session.update":
            td = msg.get("session", {}).get("turn_detection", {})
            threshold = td.get("threshold", threshold)
            silence_ms = td.get("silence_duration_ms", silence_ms)
        elif t == "input_audio_buffer.append":
            pcm = base64.b64decode(msg["audio"])
            seen_audio = True
            level = rms(pcm)
            if level > threshold:
                quiet_ms = 0
                speech_ms += CHUNK_MS
                if not speech and speech_ms >= 2 * CHUNK_MS:
                    speech = True
                    await ws.send(json.dumps(
                        {"type": "input_audio_buffer.speech_started"}))
                    cues.emit("speech_started")
            else:
                speech_ms = 0
                if speech:
                    quiet_ms += CHUNK_MS
                    if quiet_ms >= silence_ms:
                        await complete()
        elif t == "input_audio_buffer.commit":
            if speech and seen_audio:
                await complete()


async def ws_main(args, scenario, cues):
    async def handler(ws):
        try:
            await realtime(ws, scenario, cues)
        except websockets.exceptions.ConnectionClosed:
            pass

    async with websockets.serve(handler, "127.0.0.1", args.ws_port,
                                max_size=10 * 1024 * 1024):
        print("ready", flush=True)
        await asyncio.Future()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http-port", type=int, required=True)
    ap.add_argument("--ws-port", type=int, required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--cues", required=True)
    args = ap.parse_args()
    scenario = json.loads(Path(args.scenario).read_text())
    cues = Cues(args.cues)

    httpd = ThreadingHTTPServer(
        ("127.0.0.1", args.http_port), http_handler(scenario, args.ws_port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    asyncio.run(ws_main(args, scenario, cues))


if __name__ == "__main__":
    main()
