"""Runtime settings, from environment variables with listenr-compatible names.

Structured to mirror listenr's settings groups (whisper / vad / storage) so a
future `listenr dictate` merge maps field-for-field onto listenr's pydantic
settings. Kept stdlib-only here: dictatr has no reason to pull in pydantic.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass
class WhisperSettings:
    model: str = os.environ.get("DICTATE_MODEL", "Moonshine-Medium-Streaming")
    api_base: str = os.environ.get("LEMONADE_URL", "http://localhost:8080/api/v1")


@dataclass
class VADSettings:
    # Server-side VAD settings passed to Lemonade via session.update,
    # same names and defaults as listenr's VADSettings.
    threshold: float = _f("DICTATE_VAD_THRESHOLD", 0.02)
    silence_duration_ms: int = _i("DICTATE_VAD_SILENCE_MS", 1200)
    prefix_padding_ms: int = _i("DICTATE_VAD_PREFIX_MS", 250)
    max_segment_s: float = _f("DICTATE_MAX_SEC", 25.0)
    # Client-side: give up if the server reports no speech for this long.
    max_wait_s: float = _f("DICTATE_MAX_WAIT", 20.0)


@dataclass
class StorageSettings:
    # listenr-format archive: audio/YYYY-MM-DD/clip_*.wav + manifest.jsonl.
    # "off" disables archiving.
    base: str = os.environ.get(
        "DICTATE_ARCHIVE", str(Path.home() / ".listenr" / "dictation")
    )

    @property
    def enabled(self) -> bool:
        return self.base not in ("", "off")


@dataclass
class Settings:
    whisper: WhisperSettings = field(default_factory=WhisperSettings)
    vad: VADSettings = field(default_factory=VADSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    # Audio source override for tests: a wav file streamed instead of the mic.
    input_file: str | None = os.environ.get("DICTATE_INPUT") or None


settings = Settings()
