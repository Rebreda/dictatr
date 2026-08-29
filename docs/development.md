# Working on dictatr

## Get a checkout running

```bash
git clone https://github.com/Rebreda/dictatr && cd dictatr
uv sync                                    # .venv; websockets is the only dep
sudo dnf install gtk4 python3-gobject gtk4-layer-shell   # for ui/
.venv/bin/python -m pytest -q              # should be all green
```

`./install.sh` goes further: it symlinks the launchers into `~/.local/bin`,
writes the desktop entries, and autostarts the tray. You do not need it to
work on the code, only to use the checkout as your daily dictation setup.

Run things straight out of the tree:

```bash
bin/dictate type          # a dictation, same as the hotkey
bin/dictate backend status
bin/dictate-menu          # radial menu
bin/dictate-tray          # tray icon
bin/dictate-setup         # setup wizard
bin/dictate-chat          # voice chat
```

**Two pythons, on purpose.** `src/dictatr/` is stdlib plus `websockets`
and runs under the venv. Everything in `ui/` needs PyGObject, which lives
in the system python, so the `bin/` shims pick an interpreter that can
import `gi` and the package shells out to `ui/` rather than importing it.
Calling `python3 ui/menu.py` from an activated venv fails with
`No module named 'gi'`; use the shim, or `/usr/bin/python3`.

## Tests

```bash
.venv/bin/python -m pytest -q          # all of it, well under a second
.venv/bin/python -m pytest tests/test_backend.py -q
```

They are pure unit tests: no network, no audio device, no GTK. Anything
that would need those is exercised by hand or on the demo stage instead.

## Running without a microphone or a server

| Variable | Effect |
|---|---|
| `DICTATE_INPUT=clip.wav` | stream a wav instead of the mic (colon-separated list for one wav per turn in voice chat) |
| `DICTATE_INPUT_PACED=1` | feed that wav in real time rather than as fast as it reads |
| `LEMONADE_URL=...` | force one server URL, skipping all backend detection |
| `DICTATE_NO_PORTAL=1` | skip the portal typing tier (falls to ydotool, then clipboard) |
| `DICTATE_NO_SETUP=1` | stop the tray offering the setup wizard |
| `DICTATR_SETUP_STEP=N` | open the wizard straight on page N (0 to 3) |
| `RADIAL_DEMO=progress` | `python3 ui/radial.py` shows the progress bubble on its own |

There is a scripted Lemonade stand-in at `docs/demo/lib/stub_lemonade.py`:
it answers health, models, transcriptions, chat and speech from a JSON
scenario file, and runs real energy VAD over the audio you stream at it,
so dictation timing feels real while the text stays fixed. The demo
harness starts it for you; to drive it by hand:

```bash
echo '{"transcripts": ["hello from the stub"]}' > /tmp/scenario.json
: > /tmp/cues.jsonl
.venv/bin/python docs/demo/lib/stub_lemonade.py \
    --http-port 8099 --ws-port 8098 \
    --scenario /tmp/scenario.json --cues /tmp/cues.jsonl &

LEMONADE_URL=http://127.0.0.1:8099/api/v1 \
  DICTATE_INPUT=docs/demo/audio/hero.wav bin/dictate clip
wl-paste     # hello from the stub
```

`clip` rather than `type` so a test dictation lands in the clipboard
instead of in whatever window you happen to be looking at. The
`realtime connect failed ... HTTP 404` line is expected here: dictatr
tries the `/realtime` proxy on the API base first and falls back to the
dedicated websocket port that `/v1/health` advertises, which is the only
one the stub serves.

## The backend

Every server call resolves through `src/dictatr/backend/`. The CLI is the
quickest way to see what a given machine will do:

```bash
bin/dictate backend status     # provider, URL, server health, managed state
bin/dictate backend start      # download (once) and start the managed lemond
bin/dictate backend pull MODEL # with progress
bin/dictate backend stop
```

The managed instance keeps everything under
`~/.local/share/dictatr/lemonade` (port, key, logs) with the binary in
`~/.local/share/dictatr/lemond`; delete that directory to start over.
`lemond.log` there is the first place to look when it will not start.

## The setup wizard

`ui/setup.py` is four `Step` classes over one window. A step's `enter()`
probes on a worker thread and reports back through `GLib.idle_add`; the
window owns every widget and the steps only call its `set_body`,
`set_status`, `set_progress`, `set_extra` and `set_actions`. Nothing
touches GTK off the main thread and nothing blocks it, which matters
because one of these steps waits on a portal dialog and another
downloads a gigabyte.

The chrome is `ui/radial.py`: the emblem is a `ProgressBubble` (spinning
while a probe runs, filling while a download runs) at the center of the
menu's own ring geometry, orbited by one marker per step. `_show()`
rotates the orbit and slides the text block, in opposite directions for
forward and back.

`DICTATR_SETUP_STEP=N` opens straight on page N. Page 3 needs no probe
and no environment, which is why it is the one the demo stage captures.

## Where things live

| Path | What is in it |
|---|---|
| `src/dictatr/` | the package: CLI, engine, delivery, archive, backend |
| `src/dictatr/backend/` | provider config, detection, managed lemond, client |
| `ui/radial.py` | the visual kit: palette, bubbles, ring, transitions, progress arc, icon theme |
| `ui/menu.py` | radial menu and the settings window |
| `ui/setup.py` | setup wizard |
| `ui/tray.py` | tray icon and the hotkey portal host |
| `ui/chat.py` | floating voice chat |
| `ui/portal_typed.py` | RemoteDesktop portal typing helper |
| `bin/` | the launcher shims |
| `packaging/` | `stage.sh` plus the rpm and deb builders |
| `docs/demo/` | the capture harness (see its own README) |

Anything that draws should paint with `ui/radial.py`: import the palette
and geometry rather than restating hex codes or pixel sizes, and call
`radial.apply_css()` so the bundled symbolic icons resolve.

## Packages

```bash
packaging/stage.sh /tmp/tree     # exactly what a package installs
packaging/build-deb.sh           # -> dist/
packaging/build-rpm.sh           # builds from a git archive of HEAD
```

`stage.sh` is the single source of truth for both builders, so a new file
that must ship gets added there once. More in
[packaging.md](packaging.md).

## Demo assets

`docs/demo/demo stills|video|voice|shell` regenerates the screenshots and
gifs on an isolated headless sway stage. It needs a handful of tools that
are not otherwise required; see [demo/README.md](demo/README.md). Nothing
else in the project depends on it.
