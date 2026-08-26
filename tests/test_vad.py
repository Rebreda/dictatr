import asyncio
import struct

from dictatr.vad import CHUNK_MS, capture_utterance

RATE = 16000
SAMPLES = RATE * CHUNK_MS // 1000


def tone_chunk(amplitude=6000):
    return struct.pack(f"<{SAMPLES}h", *([amplitude, -amplitude] * (SAMPLES // 2)))


def silence_chunk():
    return b"\x00" * (SAMPLES * 2)


async def _gen(chunks):
    for c in chunks:
        yield c


def run(chunks):
    async def main():
        return await capture_utterance(_gen(chunks), asyncio.Event())
    return asyncio.run(main())


def test_speech_then_silence_is_captured():
    chunks = [silence_chunk()] * 5 + [tone_chunk()] * 12 + [silence_chunk()] * 50
    utt = run(chunks)
    assert utt is not None
    assert utt.speech_ms >= 250
    # captured audio includes pre-roll and the tone
    assert len(utt.pcm) >= 12 * SAMPLES * 2


def test_pure_silence_returns_none():
    assert run([silence_chunk()] * 60) is None


def test_single_click_is_not_speech():
    # one isolated loud chunk (a keyboard click) must be rejected
    chunks = [silence_chunk()] * 5 + [tone_chunk()] + [silence_chunk()] * 50
    assert run(chunks) is None


def test_listen_abs_min_rejects_ambient_bed():
    # rms ~0.034: above the close-mic trigger, below the listen floor
    quiet = [silence_chunk()] * 5 + [tone_chunk(1100)] * 30 + \
        [silence_chunk()] * 50

    async def with_floor():
        return await capture_utterance(_gen(quiet), asyncio.Event(),
                                       abs_min=0.05, min_speech_ms=400)
    assert run(quiet) is not None          # dictation would take it
    assert asyncio.run(with_floor()) is None  # listen mode rejects it
