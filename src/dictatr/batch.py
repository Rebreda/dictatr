"""Batch transcription via Lemonade's OpenAI-compatible HTTP endpoint."""

import io
import json
import urllib.request
import uuid
import wave
from pathlib import Path

from .backend import client as backend


def pcm_to_wav_bytes(pcm: bytes, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def transcribe_bytes(wav_bytes: bytes, filename: str = "clip.wav",
                     model: str | None = None) -> str:
    cap = backend.get_backend().cap("asr")
    url = f"{cap.base}/audio/transcriptions"
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n"
        f"{model or cap.model}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\nContent-Type: audio/wav\r\n\r\n".encode(),
        wav_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        **cap.headers(),
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        text = (json.load(r).get("text") or "").strip()
    if text in (".", "[BLANK_AUDIO]"):
        return ""
    return text


def batch_model() -> str:
    """A batch-capable model for `dictate file`: streaming models only
    speak /realtime (the batch endpoint answers them with "", measured
    2026-08), so fall back to Whisper for one-shot file transcription."""
    m = backend.get_backend().cap("asr").model
    return "Whisper-Large-v3-Turbo" if "streaming" in m.lower() else m


def transcribe_file(path: str) -> str:
    return transcribe_bytes(Path(path).read_bytes(), Path(path).name,
                            model=batch_model())
