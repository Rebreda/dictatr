# dictatr

Hotkey voice dictation for Linux desktops, backed by a local
[Lemonade](https://lemonade-server.ai) Whisper server. Press a hotkey, speak,
and the transcript is typed at your cursor (or copied to the clipboard).
Recording stops by itself when you stop talking. Nothing leaves your machine.

Every accepted dictation is archived in
[listenr](https://github.com/Rebreda/listenr)'s on-disk format
(`audio/YYYY-MM-DD/clip_*.wav` + append-only `manifest.jsonl`), so daily
dictation doubles as ASR fine-tuning data. The package mirrors listenr's
module layout (settings / storage / batch / realtime client) so it can be
merged into listenr as a `listenr dictate` subcommand later.

## How it decides when you stopped talking

Client-side adaptive VAD ([src/dictatr/vad.py](src/dictatr/vad.py)): the noise
floor is tracked continuously (mic auto-gain can't fake speech), an utterance
ends after a configurable pause, and captures with under 250 ms of speech are
discarded (Whisper hallucinates on breath noise). The whole utterance then
goes to Lemonade's batch `/audio/transcriptions` endpoint.

Lemonade's `/realtime` WebSocket VAD is also implemented
([src/dictatr/engine.py](src/dictatr/engine.py), `DICTATE_MODE=realtime`) but
is not the default: measured on real recordings it over-segments mid-sentence
and hallucinates on breath segments, while batch transcription of the full
utterance is flawless.

## Dependencies

Python (managed by `uv sync`, pinned in `pyproject.toml`):

- `websockets` — the realtime client. That's the only one.

System tools (all standard distro packages):

| Tool | Package (Fedora) | Used for | Required? |
|---|---|---|---|
| `pw-record` | pipewire-utils | mic capture | yes |
| `wl-copy` | wl-clipboard | clipboard delivery | yes (Wayland) |
| `notify-send` | libnotify | status feedback | yes |
| GTK4 + PyGObject | gtk4, python3-gobject | floating menu | menu only |
| `ydotool` (+ daemon) | ydotool | type at cursor | optional |
| `uv` | uv | env management | install only |

The engine itself is portable to any desktop (mic, HTTP, clipboard,
notifications are all desktop-agnostic); the hotkey registration in
`install.sh` is KDE-specific, and on GNOME/Hyprland you bind the same two
commands in your own shortcut settings.

## Install

```bash
./install.sh   # uv sync, symlinks, .desktop entries, KDE shortcut bindings
```

Assign/verify the shortcuts in System Settings → Shortcuts ("Dictate"):
Ctrl+Alt+D dictate toggle, Ctrl+Alt+M menu. For type-at-cursor:
`sudo dnf install ydotool && systemctl --user enable --now ydotool`.

## Usage

```
dictate            listen, auto-stop on silence, type at cursor
dictate clip       same, deliver to clipboard
dictate cancel     abort without transcribing
dictate file PATH  transcribe an audio file to the clipboard
dictate-menu       floating action palette (1-4, arrows+Enter, Esc)
```

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LEMONADE_URL` | `http://localhost:8080/api/v1` | Lemonade API base |
| `DICTATE_MODEL` | `Whisper-Large-v3-Turbo` | Whisper model name |
| `DICTATE_ARCHIVE` | `~/.listenr/dictation` | listenr-format archive dir, `off` to disable |
| `DICTATE_VAD_THRESHOLD` | `0.02` | speech trigger floor (matches listenr tuning) |
| `DICTATE_VAD_SILENCE_MS` | `1200` | pause that ends the utterance |
| `DICTATE_VAD_PREFIX_MS` | `250` | pre-roll kept before the first word |
| `DICTATE_MAX_SEC` | `25` | hard cap on utterance length |
| `DICTATE_MAX_WAIT` | `20` | give up when no speech for this many seconds |
| `DICTATE_MODE` | batch | `realtime` = Lemonade server-side VAD |
| `DICTATE_INPUT` | unset | stream a wav file instead of the mic (testing) |
