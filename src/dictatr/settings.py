"""Runtime settings: defaults < ~/.config/dictatr/config.toml < environment.

Every setting is declared once, as a Setting: its environment variable,
its config key, its type and its default, all in one place. Nothing else
in the codebase should know a config key as a bare string.

Settings are read when they are used, not when this module is imported.
Several processes are long-lived (the tray, the voice chat, the always-on
listener) while the settings window and the wizard write the config file
underneath them, and a value that froze at import means the same setting
is one thing in one surface and another thing next door until everything
is restarted.

Structured to mirror listenr's settings groups (whisper / vad / storage)
so a future `listenr dictate` merge maps field-for-field onto listenr's
pydantic settings. Kept stdlib-only here: dictatr has no reason to pull
in pydantic.
"""

import os
import sys
import tomllib
from pathlib import Path

CONFIG_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "dictatr" / "config.toml"
)

try:
    _cfg = tomllib.loads(CONFIG_PATH.read_text())
except (OSError, tomllib.TOMLDecodeError):
    _cfg = {}

# config key -> Setting, filled in as the groups below are defined. The
# writers validate against it, so a typo cannot quietly land a key that
# nothing ever reads.
REGISTRY: dict[str, "Setting"] = {}

# Keys owned elsewhere but living in the same flat table: the backend
# provider block (see backend/config.py) and the wizard's own marker.
_EXTRA_KEYS = frozenset(
    {"setup_done", "backend", "backend_url"}
    | {f"{cap}_{part}" for cap in ("asr", "chat", "tts", "embed")
       for part in ("url", "key", "model")}
)

_TRUE = ("1", "true", "yes", "on")

# Keys already complained about, so a bad value is reported once rather
# than on every read of a setting that is resolved on every read.
_COMPLAINED: set[str] = set()


class Setting:
    """One setting, resolved on every read: environment first (it is the
    override of last resort and must win everywhere), then the config
    file, then the default.

    A non-data descriptor on purpose: assigning to the attribute puts a
    plain value in the instance dict and shadows this, which is what
    tests and one-off overrides want.
    """

    def __init__(self, env: str, key: str, default, kind=str):
        self.env, self.key, self.default, self.kind = env, key, default, kind
        REGISTRY[key] = self

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        raw = os.environ.get(self.env) or _cfg.get(self.key)
        if raw is None or raw == "":
            raw = self.default
        return self.coerce(raw)

    def coerce(self, raw):
        """*raw* as this setting's type, falling back to the default.

        A number that will not parse is a wrong value, not a reason for
        a surface to fail to start: settings are resolved on every read,
        so `max_sec = "ninety"` in the config -- or a stray
        DICTATE_MAX_SEC in a shell -- would otherwise raise from
        whichever line happened to ask next, mid-dictation as easily as
        at startup. Said once per process so it is visible in the log
        without filling it.
        """
        if raw is None:
            return None
        if self.kind is bool:
            return raw if isinstance(raw, bool) else str(raw).lower() in _TRUE
        if self.kind is str:
            return str(raw)
        try:
            return self.kind(raw)
        except (TypeError, ValueError):
            if self.key not in _COMPLAINED:
                _COMPLAINED.add(self.key)
                print(f"dictatr: {self.key}={raw!r} is not a "
                      f"{self.kind.__name__}; using {self.default!r}",
                      file=sys.stderr)
            return self.kind(self.default)


def raw_config() -> dict:
    """The parsed config-file table, for the flat backend keys that are
    read as a block rather than one setting at a time."""
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
    updated too, so every setting read after this returns the new value.
    """
    unknown = set(updates) - set(REGISTRY) - _EXTRA_KEYS
    if unknown:
        # Not fatal: a newer surface may know keys this build does not.
        print(f"dictatr: writing unknown config keys: {sorted(unknown)}",
              file=sys.stderr)
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


def legacy_archive_pending() -> bool:
    """True when recordings are still sitting in listenr's directory.

    Only worth saying when the old place has data and the configured one
    does not: anyone who has already moved, or who points the archive
    somewhere deliberately, hears nothing."""
    if "archive" in _cfg or not LEGACY_ARCHIVE.exists():
        return False
    if not (LEGACY_ARCHIVE / "manifest.jsonl").exists():
        return False
    here = DATA_HOME / "archive"
    return not here.exists() or not any(here.iterdir())


def setup_seen() -> bool:
    """True once the wizard has run to an end (finished or dismissed).
    The tray only offers first-run setup while this is False."""
    return "setup_done" in _cfg


class WhisperSettings:
    model = Setting("DICTATE_MODEL", "model", "Moonshine-Medium-Streaming")
    api_base = Setting("LEMONADE_URL", "api_base",
                       "http://localhost:8080/api/v1")


class VADSettings:
    # Server-side VAD settings passed to Lemonade via session.update,
    # same names and defaults as listenr's VADSettings.
    threshold = Setting("DICTATE_VAD_THRESHOLD", "vad_threshold", 0.02, float)
    silence_duration_ms = Setting("DICTATE_VAD_SILENCE_MS", "silence_ms",
                                  1200, int)
    prefix_padding_ms = Setting("DICTATE_VAD_PREFIX_MS", "prefix_ms", 250, int)
    max_segment_s = Setting("DICTATE_MAX_SEC", "max_sec", 90.0, float)
    # Client-side: give up if the server reports no speech for this long.
    max_wait_s = Setting("DICTATE_MAX_WAIT", "max_wait", 20.0, float)
    # Realtime: how long after the last transcript, with no new speech,
    # the dictation is finished. Shorter pauses are segment boundaries
    # and their transcripts are joined.
    idle_s = Setting("DICTATE_IDLE_S", "idle_s", 3.0, float)


# Everything dictatr keeps: the archive below, and the managed engine
# and its working directory (see backend/lemond.py). One definition,
# because the two would silently share a directory otherwise.
DATA_HOME = (Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local"
                  / "share") / "dictatr")

# Where the archive used to live, back when it was written into
# listenr's directory. Nothing depends on listenr, so the recordings
# belong under dictatr's own name; tools/archive-migrate moves an old one.
LEGACY_ARCHIVE = Path.home() / ".listenr" / "dictation"


class StorageSettings:
    # audio/YYYY-MM-DD/clip_*.wav + an append-only manifest.jsonl, in
    # listenr's on-disk format so the tree can be handed to listenr by
    # copying it. "off" disables archiving.
    base = Setting("DICTATE_ARCHIVE", "archive", str(DATA_HOME / "archive"))

    @property
    def enabled(self) -> bool:
        return self.base not in ("", "off")


class ShotSettings:
    """Which program takes the screenshot behind Ctrl+Alt+G.

    "auto" prefers a desktop editor that selects and annotates in one
    pass (Spectacle on KDE, satty or swappy on wlroots), then falls back
    to the Screenshot portal feeding dictatr's own editor, and finally
    to the bare portal capture on a machine with no GTK.

    "dictatr" always uses ours, which is the one that looks and behaves
    the same everywhere. "portal" takes the bare capture and does no
    editing. Anything else is a command line with {path} where the
    output file goes -- the picker is a matter of taste and muscle
    memory, and somebody else's is not worth arguing with."""

    tool = Setting("DICTATE_SCREENSHOT", "screenshot", "auto")


class LLMSettings:
    # Ask mode: chat model and spoken answers (Kokoro TTS).
    model = Setting("DICTATE_LLM_MODEL", "llm_model", "Qwen3.5-4B-GGUF")
    speak = Setting("DICTATE_SPEAK", "speak_answers", True, bool)
    tts_model = Setting("DICTATE_TTS_MODEL", "tts_model", "kokoro-v1")
    tts_voice = Setting("DICTATE_TTS_VOICE", "tts_voice", "af_heart")
    # Ask-mode recall: semantic search over the dictation archive.
    recall = Setting("DICTATE_RECALL", "recall", True, bool)
    embed_model = Setting("DICTATE_EMBED_MODEL", "embed_model",
                          "nomic-embed-text-v1-GGUF")
    # Context ask mode may read from the desktop: comma-separated source
    # names (selection, clipboard), empty for none. Selection is on
    # because highlighting text is a deliberate "I mean this".
    context = Setting("DICTATE_ASK_CONTEXT", "ask_context", "selection")
    # Tag archived dictations with LLM-extracted concepts.
    concepts = Setting("DICTATE_CONCEPTS", "concepts", True, bool)
    # Show the chat's working: which context was read, what was recalled
    # from the archive, every tool the model called and what it answered.
    # Off by default -- an answer is the point, the trace is the audit.
    details = Setting("DICTATE_CHAT_DETAILS", "chat_details", False, bool)


class NotifySettings:
    # Which notification categories show. State chatter (listening /
    # transcribing) shares one replaceable bubble; the rest pop fresh.
    state = Setting("DICTATE_NOTIFY_STATE", "notify_state", True, bool)
    delivery = Setting("DICTATE_NOTIFY_DELIVERY", "notify_delivery", True, bool)
    answers = Setting("DICTATE_NOTIFY_ANSWERS", "notify_answers", True, bool)
    toggles = Setting("DICTATE_NOTIFY_TOGGLES", "notify_toggles", True, bool)
    errors = Setting("DICTATE_NOTIFY_ERRORS", "notify_errors", True, bool)


class ListenSettings:
    # Always-on mode. Tagging every ambient utterance keeps the LLM warm
    # around the clock, so it is opt-in unlike interactive rows.
    tag = Setting("DICTATE_LISTEN_TAG", "listen_tag", False, bool)


class GCSettings:
    # dictatr gc: listen-mode rows shorter than min_duration_s AND with
    # fewer words than min_words are quarantined; trash older than
    # purge_days is deleted for good.
    min_duration_s = Setting("DICTATE_GC_MIN_SEC", "gc_min_sec", 1.0, float)
    min_words = Setting("DICTATE_GC_MIN_WORDS", "gc_min_words", 2, int)
    purge_days = Setting("DICTATE_GC_PURGE_DAYS", "gc_purge_days", 30.0, float)


class TypingSettings:
    # Portal keysym injection can leave the compositor's modifier state
    # desynced on some desktops, which makes the whole session act as
    # though Ctrl is held. Escape hatch: portal_typing = false falls back
    # to the clipboard instead.
    portal = Setting("DICTATE_PORTAL_TYPING", "portal_typing", True, bool)
    # Type the transcript as it is dictated instead of all at once when
    # the utterance ends. The engine revises words it has already sent,
    # so the cursor visibly backspaces over a correction; turn this off
    # for a single clean insert at the end.
    live = Setting("DICTATE_LIVE_TYPING", "live_typing", True, bool)


class GestureSettings:
    """Pointer gestures, and which shortcut each performs.

    A value is a shortcut id from ui/shortcuts.py (chat, menu, suggest,
    dictate, listen, cancel, shot); empty means the gesture does
    nothing. KDE only: the compositor is what sees the pointer."""

    shake_v = Setting("DICTATE_GESTURE_SHAKE_V", "gesture_shake_v", "chat")
    shake_h = Setting("DICTATE_GESTURE_SHAKE_H", "gesture_shake_h", "")
    circle_cw = Setting("DICTATE_GESTURE_CIRCLE_CW", "gesture_circle_cw", "")
    circle_ccw = Setting("DICTATE_GESTURE_CIRCLE_CCW", "gesture_circle_ccw", "")


class DebugSettings:
    """Which diagnostics are on: a comma-separated list of topics, or
    "all". Empty by default, and deliberately absent from the settings
    window and the wizard -- these are firehoses for tuning and for bug
    reports, not preferences, and a checkbox only invites turning one on
    by accident.

    A list rather than one switch because the topics are unrelated and
    each is loud: whoever is tuning a gesture should not have to read
    everything else at the same time.

    Topics:
      gesture  every trace the compositor hands over, with the
               measurements behind the verdict (src/dictatr/gestures.py)

    Read live, like every other setting, so a topic can be turned on in
    the config file without restarting anything.
    """

    topics = Setting("DICTATE_DEBUG", "debug", "")

    def __contains__(self, topic: str) -> bool:
        wanted = {t.strip() for t in self.topics.split(",") if t.strip()}
        return "all" in wanted or topic in wanted


class Settings:
    # Audio source override for tests: a wav file streamed instead of
    # the mic. No config key: it is a test seam, not a preference.
    input_file = Setting("DICTATE_INPUT", "input_file", None)

    def __init__(self):
        self.whisper = WhisperSettings()
        self.typing = TypingSettings()
        self.llm = LLMSettings()
        self.vad = VADSettings()
        self.storage = StorageSettings()
        self.shot = ShotSettings()
        self.listen = ListenSettings()
        self.gc = GCSettings()
        self.notify = NotifySettings()
        self.gestures = GestureSettings()
        self.debug = DebugSettings()


settings = Settings()
