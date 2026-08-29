# Demo harness

Reproducible screenshots and demo videos for the README, captured on an
isolated headless desktop so nothing depends on (or disturbs) the real
session. Every asset shares one motif: the palette in
[stage/wallpaper.svg](stage/wallpaper.svg), the staged notes editor, the
top bar with the live tray icon.

```bash
./docs/demo/demo stills          # docs/assets/desktop-menu.png, -setup, -chat
./docs/demo/demo video           # docs/assets/hero.gif  (notes scene, hotkey path)
./docs/demo/demo video chat      # docs/assets/chat.gif  (DM scene, radial-menu path)
./docs/demo/demo video voicechat # docs/assets/voicechat.gif  (two-turn AI chat)
./docs/demo/demo voice           # resynthesize voices (needs Lemonade + kokoro-v1)
./docs/demo/demo shell           # boot the stage and hold it for manual poking
```

Dependencies (Fedora names):

```bash
sudo dnf install sway grim wf-recorder wtype mako foot nano \
                 librsvg2-tools ffmpeg python3-pywayland python3-pillow
```

No system install handy? Point `DICTATR_DEMO_TOOLS` at colon-separated
prefixes containing extracted RPMs (`usr/bin`, `usr/lib64`).

## How it works

`lib/session.py` boots the stage: headless sway (3200x1800 @ 2x: crisp
captures, and the video's zooms magnify real pixels), a private session
bus with **no activatable services** (a stock bus would drag the host's
xdg-desktop-portal stack in and stall GTK), mako for notifications, a
Lemonade stub, and a persistent virtual pointer. Isolation from the host
comes from a private `XDG_RUNTIME_DIR`, `XDG_CONFIG_HOME`
([stage/xdg](stage/xdg)) and `DBUS_SESSION_BUS_ADDRESS`; the compositor
socket is shared via an absolute `WAYLAND_DISPLAY` path.

`lib/stub_lemonade.py` stands in for Lemonade: scripted transcripts over
the `/realtime` websocket (with real energy-VAD on the streamed audio, so
notification timing tracks the voice), plus the health/models/batch/chat
endpoints. Every take is identical: no models, no GPU, no ASR variance.

`lib/pointerd.py` holds a wlr-virtual-pointer open for the whole session.
Headless sway renders no cursor unless the seat has a pointer device, and
one-shot injectors (wlrctl) destroy theirs on exit. Movement and clicks
go through it; typing goes through wtype via a `ydotool` shim on PATH
(real ydotool is uinput; its events would land on the *host* desktop).

`lib/director.py` drives scenes: eased pointer glides, window placement
over sway IPC, cue logging (`cues.jsonl`), grim screenshots, wf-recorder
capture (lossless ffv1, sliced+threaded; single-threaded ffv1 can't keep
up with 4K@30 and silently truncates).

`lib/kenburns.py` is the camera: keyframed zoom/pan (smoothstep-eased)
between cue-anchored positions, rendered from the 4K master to a
logical-resolution mp4 + gif, with screen-space keycap overlays
("Ctrl + Alt + D"). Cue times map onto video time via a sync flash at the
start of each recording; the capture is damage-driven VFR, rebuilt to a
constant-rate timeline from its real timestamps at decode.

Scenes live in [scenes/](scenes); `stills.py` (desktop compositions),
`hero.py` (hotkey dictation into a notes editor), `chat.py` (a reply
dictated into a messenger via the radial menu; the Signal-style DM window
is a GTK prop, [stage/chat.py](stage/chat.py)) and `voicechat.py` (a
two-turn conversation with the floating voice chat: scripted word-level
deltas stream into the live pill, per-turn answers come from the stub's
chat endpoint). Voice lines are synthesized once with the project's own
TTS stack (kokoro via Lemonade) into `audio/<scene>[-N].wav` (gitignored;
`demo voice` regenerates them) and streamed into dictatr through
`DICTATE_INPUT` — one wav per conversation turn — the same path the
tests use, so the app under capture is the real app end to end.
