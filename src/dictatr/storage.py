"""Persist dictations in listenr's on-disk format.

Port of listenr's storage.save_recording with the numpy/soundfile dependency
replaced by stdlib wave (we already hold PCM16 bytes). Field names, layout
and manifest semantics are kept identical so listenr tooling can read the
archive; `source` is the one extra field, marking rows written by dictatr.

Layout:
    <base>/
        audio/YYYY-MM-DD/clip_YYYY-MM-DD_<uid>.wav
        manifest.jsonl          <- append-only, one JSON object per line
"""

import json
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path


def save_recording(
    pcm_bytes: bytes,
    raw_text: str,
    corrected_text: str | None = None,
    *,
    storage_base: Path,
    asr_rate: int = 16000,
    whisper_model: str = "",
) -> dict:
    if not pcm_bytes:
        raise ValueError("Refusing to save a clip with no audio.")

    ts = datetime.now(timezone.utc)
    date_str = ts.strftime("%Y-%m-%d")
    uid = uuid.uuid4().hex[:12]

    audio_dir = storage_base / "audio" / date_str
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"clip_{date_str}_{uid}.wav"

    with wave.open(str(audio_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(asr_rate)
        w.writeframes(pcm_bytes)

    record = {
        "uuid": uid,
        "timestamp": ts.isoformat(),
        "audio_path": str(audio_path),
        "raw_transcription": raw_text,
        "corrected_transcription": corrected_text if corrected_text else raw_text,
        "is_improved": False,
        "categories": [],
        "whisper_model": whisper_model,
        "llm_model": None,
        "duration_s": round(len(pcm_bytes) / 2 / asr_rate, 3),
        "sample_rate": asr_rate,
        "source": "dictatr",
    }

    manifest = storage_base / "manifest.jsonl"
    with open(manifest, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
