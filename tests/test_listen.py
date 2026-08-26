import asyncio
import json

from dictatr import listen
from dictatr.settings import settings


def rows(base):
    return [json.loads(line) for line in
            (base / "manifest.jsonl").read_text().splitlines()]


def test_archive_writes_listen_row(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.storage, "base", str(tmp_path))
    rec = listen.archive("hello from the room", b"\x00\x01" * 16000)
    assert rec is not None
    (row,) = rows(tmp_path)
    assert row["raw_transcription"] == "hello from the room"
    assert row["meta"]["mode"] == "listen"
    assert row["duration_s"] == 1.0


def test_archive_skips_empty_and_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.storage, "base", str(tmp_path))
    assert listen.archive("", b"\x00\x01" * 800) is None
    assert listen.archive("words", b"") is None
    monkeypatch.setattr(settings.storage, "base", "off")
    assert listen.archive("words", b"\x00\x01" * 800) is None
    assert not (tmp_path / "manifest.jsonl").exists()


def test_run_archives_streamed_utterances(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.storage, "base", str(tmp_path))
    monkeypatch.setattr(settings, "input_file", "unused.wav")
    monkeypatch.setattr(listen, "pin_model", lambda: None)

    async def fake_stream(source, stop, on_state=lambda s: None):
        yield "first utterance", b"\x00\x01" * 8000
        yield "second utterance", b"\x00\x01" * 8000

    async def fake_source(path, stop):
        return
        yield  # pragma: no cover — make it an async generator

    monkeypatch.setattr(listen, "stream_utterances", fake_stream)
    monkeypatch.setattr(listen.mic, "file_chunks", fake_source)
    assert asyncio.run(listen._run()) == 0
    texts = [r["raw_transcription"] for r in rows(tmp_path)]
    assert texts == ["first utterance", "second utterance"]
