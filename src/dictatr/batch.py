"""Batch transcription via Lemonade's OpenAI-compatible HTTP endpoint."""

import io
import json
import urllib.error
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
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = (json.load(r).get("text") or "").strip()
    except urllib.error.HTTPError as e:
        raise ConnectionError(_server_said(e, model or cap.model)) from e
    except (urllib.error.URLError, OSError) as e:
        raise ConnectionError(f"cannot reach {cap.base}: {e}") from e
    if text in (".", "[BLANK_AUDIO]"):
        return ""
    return text


def _server_said(e, model: str) -> str:
    """The server's own words, and what to do about the one that keeps
    happening.

    A backend that has the streaming model pinned -- which is the state
    dictatr's own setup leaves it in -- refuses to load the batch model
    beside it, so `dictate file` fails on a working install. That is
    worth saying in a sentence rather than as a stack trace, and worth
    naming the fix for.
    """
    try:
        detail = json.loads(e.read().decode()).get("error") or {}
    except (ValueError, OSError):
        detail = {}
    said = detail.get("message") or f"HTTP {e.code} {e.reason}"
    if detail.get("code") == "slots_pinned_error":
        # Deliberately no command: the remedy depends on who runs the
        # engine. A managed backend answers `dictate backend stop`; a
        # system one was started by something else and telling the user
        # to run ours would be advice that quietly does nothing.
        return (f"{said} The engine is holding the dictation model "
                f"pinned, so {model} cannot load beside it -- free a "
                f"transcription slot in the engine and try again "
                f"(`dictate backend status` says which engine this is).")
    return said


def batch_model() -> str:
    """A batch-capable model for `dictate file`: streaming models only
    speak /realtime (the batch endpoint answers them with "", measured
    2026-08), so fall back to Whisper for one-shot file transcription."""
    m = backend.get_backend().cap("asr").model
    return "Whisper-Large-v3-Turbo" if "streaming" in m.lower() else m


def transcribe_file(path: str) -> str:
    return transcribe_bytes(Path(path).read_bytes(), Path(path).name,
                            model=batch_model())
