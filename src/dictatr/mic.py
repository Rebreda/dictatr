"""Audio sources yielding raw PCM16 mono 16 kHz chunks.

The mic path shells out to PipeWire's pw-record rather than binding an audio
library, so the package stays pure-Python. The interface (an async generator
of PCM16 bytes) matches what listenr's stream_transcribe consumes, so a
sounddevice-based source can be swapped in for a future listenr merge.
"""

import asyncio
import contextlib
import wave

RATE = 16000
CHUNK_MS = 30
CHUNK_BYTES = RATE * CHUNK_MS // 1000 * 2

PW_RECORD = [
    "pw-record", "--raw",
    "--rate", str(RATE), "--channels", "1", "--format", "s16",
    "-",
]


async def mic_chunks(stop: asyncio.Event):
    """Yield PCM16 chunks from the default microphone until *stop* is set."""
    proc = await asyncio.create_subprocess_exec(
        *PW_RECORD,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    buf = bytearray()
    try:
        while not stop.is_set():
            read = asyncio.ensure_future(proc.stdout.read(CHUNK_BYTES))
            waited = asyncio.ensure_future(stop.wait())
            done, _ = await asyncio.wait(
                {read, waited}, return_when=asyncio.FIRST_COMPLETED
            )
            if read in done:
                waited.cancel()
                data = read.result()
                if not data:
                    break
                # Emit exact fixed-size chunks so downstream per-chunk
                # timing (silence accounting) stays accurate.
                buf.extend(data)
                while len(buf) >= CHUNK_BYTES:
                    yield bytes(buf[:CHUNK_BYTES])
                    del buf[:CHUNK_BYTES]
            else:
                read.cancel()
                break
    finally:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        await proc.wait()


async def file_chunks(path: str, stop: asyncio.Event, realtime: bool = False):
    """Yield PCM16 chunks from a wav file (16 kHz mono). Test substitute for
    the mic; *realtime* paces chunks at wall-clock speed."""
    with wave.open(path, "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1, (
            f"{path}: expected {RATE} Hz mono"
        )
        frames_per_chunk = RATE * CHUNK_MS // 1000
        while not stop.is_set():
            chunk = w.readframes(frames_per_chunk)
            if not chunk:
                break
            yield chunk
            if realtime:
                await asyncio.sleep(CHUNK_MS / 1000)
