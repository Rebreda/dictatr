"""Runtime settings: defaults < ~/.config/dictatr/config.toml < environment.

Structured to mirror listenr's settings groups (whisper / vad / storage) so a
future `listenr dictate` merge maps field-for-field onto listenr's pydantic
settings. Kept stdlib-only here: dictatr has no reason to pull in pydantic.

The config file is written by the settings UI (ui/menu.py --settings) and is
a flat table of the keys named below; environment variables always win.
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "dictatr" / "config.toml"
)

try:
    _cfg = tomllib.loads(CONFIG_PATH.read_text())
except (OSError, tomllib.TOMLDecodeError):
    _cfg = {}


def _s(env: str, key: str, default: str) -> str:
    return os.environ.get(env) or str(_cfg.get(key, default))


def _f(env: str, key: str, default: float) -> float:
    return float(os.environ.get(env) or _cfg.get(key, default))


def _i(env: str, key: str, default: int) -> int:
    return int(os.environ.get(env) or _cfg.get(key, default))


@dataclass
class WhisperSettings:
    model: str = _s("DICTATE_MODEL", "model", "Moonshine-Medium-Streaming")
    api_base: str = _s("LEMONADE_URL", "api_base", "http://localhost:8080/api/v1")


@dataclass
class VADSettings:
    # Server-side VAD settings passed to Lemonade via session.update,
    # same names and defaults as listenr's VADSettings.
    threshold: float = _f("DICTATE_VAD_THRESHOLD", "vad_threshold", 0.02)
    silence_duration_ms: int = _i("DICTATE_VAD_SILENCE_MS", "silence_ms", 1200)
    prefix_padding_ms: int = _i("DICTATE_VAD_PREFIX_MS", "prefix_ms", 250)
    max_segment_s: float = _f("DICTATE_MAX_SEC", "max_sec", 90.0)
    # Client-side: give up if the server reports no speech for this long.
    max_wait_s: float = _f("DICTATE_MAX_WAIT", "max_wait", 20.0)
    # Realtime mode: how long after the last transcript, with no new speech,
    # the dictation is considered finished. Pauses shorter than this just
    # become segment boundaries and the transcripts are joined.
    idle_s: float = _f("DICTATE_IDLE_S", "idle_s", 3.0)


@dataclass
class StorageSettings:
    # listenr-format archive: audio/YYYY-MM-DD/clip_*.wav + manifest.jsonl.
    # "off" disables archiving.
    base: str = _s(
        "DICTATE_ARCHIVE", "archive", str(Path.home() / ".listenr" / "dictation")
    )

    @property
    def enabled(self) -> bool:
        return self.base not in ("", "off")


@dataclass
class LLMSettings:
    # Ask mode: chat model and spoken answers (Kokoro TTS), all via Lemonade.
    model: str = _s("DICTATE_LLM_MODEL", "llm_model", "Qwen3.5-4B-GGUF")
    speak: bool = _s("DICTATE_SPEAK", "speak_answers", "true").lower() in (
        "1", "true", "yes", "on")
    tts_model: str = _s("DICTATE_TTS_MODEL", "tts_model", "kokoro-v1")
    tts_voice: str = _s("DICTATE_TTS_VOICE", "tts_voice", "af_heart")
    # Ask-mode recall: semantic search over the dictation archive.
    recall: bool = _s("DICTATE_RECALL", "recall", "true").lower() in (
        "1", "true", "yes", "on")
    embed_model: str = _s("DICTATE_EMBED_MODEL", "embed_model",
                          "nomic-embed-text-v1-GGUF")
    # Tag archived dictations with LLM-extracted concepts.
    concepts: bool = _s("DICTATE_CONCEPTS", "concepts", "true").lower() in (
        "1", "true", "yes", "on")


@dataclass
class Settings:
    whisper: WhisperSettings = field(default_factory=WhisperSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    vad: VADSettings = field(default_factory=VADSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    # Audio source override for tests: a wav file streamed instead of the mic.
    input_file: str | None = os.environ.get("DICTATE_INPUT") or None


settings = Settings()
