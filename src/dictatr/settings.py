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


def raw_config() -> dict:
    """The parsed config-file table. backend/config.py reads its flat
    backend keys from here at call time (testable, unlike the dataclass
    defaults below which bind at import)."""
    return _cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_config(updates: dict) -> None:
    """Merge *updates* into the config file, keeping every other key.

    Two writers exist (the settings window and the setup wizard) and each
    only knows its own keys, so a whole-file rewrite would silently drop
    the other's. A None value removes the key. The in-process table is
    updated too, so backend/config.py sees the change without a restart.
    """
    merged = dict(_cfg)
    for k, v in updates.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        "\n".join(f"{k} = {_toml_value(v)}" for k, v in merged.items()) + "\n")
    _cfg.clear()
    _cfg.update(merged)


def setup_seen() -> bool:
    """True once the wizard has run to an end (finished or dismissed).
    The tray only offers first-run setup while this is False."""
    return "setup_done" in _cfg


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


def _b(env: str, key: str, default: str) -> bool:
    return _s(env, key, default).lower() in ("1", "true", "yes", "on")


@dataclass
class NotifySettings:
    # Which notification categories show. State chatter (listening /
    # transcribing) shares one replaceable bubble; the rest pop fresh.
    state: bool = _b("DICTATE_NOTIFY_STATE", "notify_state", "true")
    delivery: bool = _b("DICTATE_NOTIFY_DELIVERY", "notify_delivery", "true")
    answers: bool = _b("DICTATE_NOTIFY_ANSWERS", "notify_answers", "true")
    toggles: bool = _b("DICTATE_NOTIFY_TOGGLES", "notify_toggles", "true")
    errors: bool = _b("DICTATE_NOTIFY_ERRORS", "notify_errors", "true")


@dataclass
class ListenSettings:
    # Always-on mode (dictatr listen). Tagging every ambient utterance keeps
    # the LLM warm around the clock, so it's opt-in unlike interactive rows.
    tag: bool = _s("DICTATE_LISTEN_TAG", "listen_tag", "false").lower() in (
        "1", "true", "yes", "on")


@dataclass
class GCSettings:
    # dictatr gc: listen-mode rows shorter than min_duration_s AND fewer
    # words than min_words are quarantined; trash older than purge_days is
    # deleted for good.
    min_duration_s: float = _f("DICTATE_GC_MIN_SEC", "gc_min_sec", 1.0)
    min_words: int = _i("DICTATE_GC_MIN_WORDS", "gc_min_words", 2)
    purge_days: float = _f("DICTATE_GC_PURGE_DAYS", "gc_purge_days", 30.0)


@dataclass
class TypingSettings:
    # Portal keysym injection can leave the compositor's modifier state
    # desynced on some desktops, which makes the whole session act as
    # though Ctrl is held. Escape hatch while that is being chased:
    # portal_typing = false falls back to the clipboard instead.
    portal: bool = _b("DICTATE_PORTAL_TYPING", "portal_typing", "true")
    # Type the transcript as it is dictated instead of all at once when
    # the utterance ends. The engine revises words it has already sent,
    # so the cursor visibly backspaces over a correction; turn this off
    # for a single clean insert at the end.
    live: bool = _b("DICTATE_LIVE_TYPING", "live_typing", "true")


@dataclass
class Settings:
    whisper: WhisperSettings = field(default_factory=WhisperSettings)
    typing: TypingSettings = field(default_factory=TypingSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    vad: VADSettings = field(default_factory=VADSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    listen: ListenSettings = field(default_factory=ListenSettings)
    gc: GCSettings = field(default_factory=GCSettings)
    notify: NotifySettings = field(default_factory=NotifySettings)
    # Audio source override for tests: a wav file streamed instead of the mic.
    input_file: str | None = os.environ.get("DICTATE_INPUT") or None


settings = Settings()
