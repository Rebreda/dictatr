"""Adaptive RMS-energy voice activity detection over a PCM16 chunk stream.

Why client-side VAD when Lemonade has a /realtime endpoint with VAD built in?
Measured on real recordings (2026-08), Lemonade's server VAD over-segments
(splitting mid-sentence regardless of silence_duration_ms), hallucinates
"Thank you." on breath-only segments, and can drop trailing speech — while
the batch /audio/transcriptions endpoint transcribes whole utterances
flawlessly. So dictatr finds the utterance boundary itself and sends one
complete clip to the batch API. The realtime client in engine.py remains as
an alternate mode.

The detector tracks the noise floor adaptively (drops instantly, rises
slowly) so microphone auto-gain can't fake permanent speech: speech starts
when a chunk's RMS exceeds max(TRIGGER_RATIO x floor, abs_min), and the
utterance ends after silence_duration_ms of chunks below both
HOLD_RATIO x floor and PEAK_FRACTION x the decaying speech peak.
"""

import math
from array import array
from collections import deque
from dataclasses import dataclass

from .settings import settings

RATE = 16000
CHUNK_MS = 30

TRIGGER_RATIO = 2.2
HOLD_RATIO = 1.4
PEAK_FRACTION = 0.25
FLOOR_RISE = 0.003   # max floor growth per chunk (~10%/s)
PEAK_DECAY = 0.995
ABS_MIN_DEFAULT = 0.015
MIN_SPEECH_MS = 250


def _rms(chunk: bytes) -> float:
    samples = array("h", chunk[: len(chunk) & ~1])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0


@dataclass
class Utterance:
    pcm: bytes
    speech_ms: int


async def capture_utterance(chunks, stop_now, on_state=lambda s: None, *,
                            abs_min=None, min_speech_ms=None):
    """Consume an async PCM16 chunk stream until one utterance ends.

    Returns an Utterance, or None if no (sufficient) speech arrived before
    settings.vad.max_wait_s / the stream ended. When *stop_now* is set the
    capture ends immediately with whatever has been collected.

    *abs_min* / *min_speech_ms* override the close-mic defaults; listen
    mode passes stricter values so an ambient noise bed (fan, typing)
    sitting near the dictation threshold can't trigger or drag segments.
    """
    vad = settings.vad
    if abs_min is None:
        abs_min = min(vad.threshold, ABS_MIN_DEFAULT) if vad.threshold \
            else ABS_MIN_DEFAULT
    min_speech = MIN_SPEECH_MS if min_speech_ms is None else min_speech_ms
    silence_ms_limit = vad.silence_duration_ms
    preroll = deque(maxlen=max(1, vad.prefix_padding_ms // CHUNK_MS))

    frames: list[bytes] = []
    floor = None
    peak = 0.0
    in_speech = False
    speech_ms = 0
    silent_ms = 0
    waited_ms = 0
    above_run = 0
    on_state("listening")

    async for chunk in chunks:
        rms = _rms(chunk)
        floor = rms if floor is None else min(rms, floor * (1 + FLOOR_RISE) + 1e-5)
        trigger = max(floor * TRIGGER_RATIO, abs_min)

        if in_speech:
            frames.append(chunk)
            peak = max(rms, peak * PEAK_DECAY)
            # abs_min also floors the hold level: without it a noise bed
            # sitting just above the adaptive floor keeps resetting the
            # silence countdown and segments drag on for 15-20s.
            hold = max(floor * HOLD_RATIO, peak * PEAK_FRACTION,
                       abs_min * 0.7)
            if rms < hold:
                silent_ms += CHUNK_MS
                above_run = 0
            else:
                # Debounce: an isolated loud chunk (keyboard click, bump) must
                # not reset the silence countdown; real speech sustains >60ms.
                above_run += 1
                if above_run >= 2:
                    silent_ms = 0
                    speech_ms += CHUNK_MS
                else:
                    silent_ms += CHUNK_MS
            if silent_ms >= silence_ms_limit:
                break
            if len(frames) * CHUNK_MS / 1000 >= vad.max_segment_s:
                break
        else:
            if rms >= trigger:
                in_speech = True
                peak = rms
                speech_ms = CHUNK_MS
                frames.extend(preroll)
                frames.append(chunk)
                on_state("speech")
            else:
                preroll.append(chunk)
                waited_ms += CHUNK_MS
                if waited_ms / 1000 >= vad.max_wait_s:
                    return None
        if stop_now.is_set():
            break

    if not in_speech or speech_ms < min_speech:
        return None
    return Utterance(pcm=b"".join(frames), speech_ms=speech_ms)
