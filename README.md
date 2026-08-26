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

With the default Moonshine streaming model, Lemonade's `/realtime` WebSocket
does the VAD server-side with the model's bundled neural TEN-VAD
([src/dictatr/engine.py](src/dictatr/engine.py)) — robust against keyboard
clicks and mic auto-gain, with word-level streaming deltas.

Non-streaming models (Whisper) automatically fall back to client-side
adaptive VAD + batch transcription ([src/dictatr/vad.py](src/dictatr/vad.py)):
whisper.cpp's realtime VAD over-segments mid-sentence and hallucinates on
breath segments (measured on real recordings), while batch transcription of
the client-detected utterance is flawless. Force a mode with
`DICTATE_MODE=realtime|batch`.

## Ask mode

The ask bubble (or `dictatr ask`) captures a spoken question and answers it
with a local LLM via Lemonade — answer on the clipboard, notification
preview, and optionally spoken aloud with Kokoro TTS (toggle in Settings or
`speak_answers = false`). Before answering, the question is semantically
matched against your dictation archive (listenr's embed-once-and-cache
approach, via Lemonade's `/embeddings` endpoint and the 75 MB
nomic-embed-text model) and relevant past dictations are given to the LLM —
so "what did I say about X" works. Disable with `recall = false`.

The default ask model is Qwen3.5-4B with thinking disabled: ~2 s answers
warm. Reasoning models (gpt-oss, larger Qwen) work but push voice latency
to 15-45 s.

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

Assign/verify the shortcuts in System Settings → Shortcuts ("Dictate") —
left-hand defaults: Ctrl+Alt+D dictate toggle, Ctrl+Alt+Space menu,
Ctrl+Alt+C cancel. (Plasma loads shortcut config at login.) For type-at-cursor:
`sudo dnf install ydotool && systemctl --user enable --now ydotool`.

## Usage

```
dictate            listen, auto-stop on silence, type at cursor
dictate clip       same, deliver to clipboard
dictate cancel     abort without transcribing
dictate file PATH  transcribe an audio file to the clipboard
dictate-menu       floating radial menu (toggle; 1-4 keys, Esc)
```

The menu appears at the cursor via a transparent layer-shell overlay when
`gtk4-layer-shell` is installed (KDE/wlroots compositors; click anywhere
outside to dismiss): `sudo dnf install gtk4-layer-shell`. Without it (GNOME
has no layer-shell) it is a small centered window.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LEMONADE_URL` | `http://localhost:8080/api/v1` | Lemonade API base |
| `DICTATE_MODEL` | `Moonshine-Medium-Streaming` | ASR model (streaming models use realtime mode) |
| `DICTATE_ARCHIVE` | `~/.listenr/dictation` | listenr-format archive dir, `off` to disable |
| `DICTATE_VAD_THRESHOLD` | `0.02` | speech trigger floor (matches listenr tuning) |
| `DICTATE_VAD_SILENCE_MS` | `1200` | pause that ends the utterance |
| `DICTATE_VAD_PREFIX_MS` | `250` | pre-roll kept before the first word |
| `DICTATE_MAX_SEC` | `25` | hard cap on utterance length |
| `DICTATE_MAX_WAIT` | `20` | give up when no speech for this many seconds |
| `DICTATE_MODE` | batch | `realtime` = Lemonade server-side VAD |
| `DICTATE_LLM_MODEL` | `Qwen3.5-4B-GGUF` | ask-mode chat model |
| `DICTATE_RECALL` | `true` | ask-mode semantic recall over the archive |
| `DICTATE_EMBED_MODEL` | `nomic-embed-text-v1-GGUF` | recall embedding model |
| `DICTATE_SPEAK` | `true` | speak ask answers via Kokoro TTS |
| `DICTATE_INPUT` | unset | stream a wav file instead of the mic (testing) |

## License

MIT — see [LICENSE](LICENSE).
