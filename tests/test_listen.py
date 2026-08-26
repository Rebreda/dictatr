import asyncio
import json
import struct
import wave

from dictatr import listen
from dictatr.settings import settings
from dictatr.vad import CHUNK_MS

RATE = 16000
SAMPLES = RATE * CHUNK_MS // 1000


def make_wav(path, utterances=1):
    tone = struct.pack(f"<{SAMPLES}h", *([6000, -6000] * (SAMPLES // 2)))
    silence = b"\x00" * (SAMPLES * 2)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        for _ in range(utterances):
            # 16 tone chunks = 480ms: over listen's min_speech_ms (400)
            w.writeframes(silence * 5 + tone * 16 + silence * 50)


def rows(base):
    return [json.loads(line) for line in
            (base / "manifest.jsonl").read_text().splitlines()]


def run_listen(tmp_path, monkeypatch, transcribe):
    wav = tmp_path / "in.wav"
    make_wav(wav)
    base = tmp_path / "archive"
    monkeypatch.setattr(settings.storage, "base", str(base))
    monkeypatch.setattr(settings, "input_file", str(wav))
    monkeypatch.setattr(listen, "transcribe_bytes", transcribe)
    monkeypatch.setattr(listen, "RETRY_DELAYS", ())
    assert asyncio.run(listen._run()) == 0
    return base


def test_listen_archives_each_utterance(tmp_path, monkeypatch):
    base = run_listen(tmp_path, monkeypatch, lambda b, model=None: "hello from tape")
    (row,) = rows(base)
    assert row["raw_transcription"] == "hello from tape"
    assert row["meta"]["mode"] == "listen"
    assert not row["meta"].get("pending_transcription")


def test_lemonade_down_archives_pending_then_retries(tmp_path, monkeypatch):
    def down(_, model=None):
        raise OSError("connection refused")
    base = run_listen(tmp_path, monkeypatch, down)
    (row,) = rows(base)
    assert row["raw_transcription"] == ""
    assert row["meta"]["pending_transcription"]

    # Lemonade back up: the next listen start backfills the transcript.
    monkeypatch.setattr(listen, "transcribe_bytes", lambda b, model=None: "recovered")
    assert listen.retry_pending() == 1
    (row,) = rows(base)
    assert row["raw_transcription"] == "recovered"
    assert not row["meta"]["pending_transcription"]
