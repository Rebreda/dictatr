import io
import wave

from dictatr.batch import pcm_to_wav_bytes


def test_pcm_to_wav_roundtrip():
    pcm = b"\x01\x02" * 4800
    data = pcm_to_wav_bytes(pcm)
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.readframes(w.getnframes()) == pcm
