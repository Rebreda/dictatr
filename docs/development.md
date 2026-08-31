# Working on dictatr

## Get a checkout running

```bash
git clone https://github.com/Rebreda/dictatr && cd dictatr
uv sync                                    # .venv; websockets is the only dep
sudo dnf install gtk4 python3-gobject gtk4-layer-shell   # for ui/
.venv/bin/python -m pytest -q              # should be all green
```

`./install.sh` goes further: it symlinks the launchers into `~/.local/bin`,
writes the desktop entries (including the one the desktop portal needs to
recognize the app at all), and starts the tray. Run it once; after that
`./dev` is the day-to-day tool.

**Two pythons, on purpose.** `src/dictatr/` is stdlib plus `websockets`
and runs under the venv. Everything in `ui/` needs PyGObject, which lives
in the system python, so the `bin/` shims pick an interpreter that can
import `gi` and the package shells out to `ui/` rather than importing it.
Calling `python3 ui/menu.py` from an activated venv fails with
`No module named 'gi'`; use the shim, or `/usr/bin/python3`.

## The everyday loop

```bash
./dev restart    # stop what is running, start the tray from this checkout
./dev status     # what is running, from where, what backend it found
./dev logs       # follow the tray log
./dev test       # pytest
./dev doctor     # find a second dictatr competing with this one
./dev keywatch   # print global-shortcut events as they fire
```

**Restart after almost any change.** The tray is the resident process: it
owns the global hotkeys, the setup offer, and the recording state icon,
and it launches the menu, the wizard and the chat as children. An edit to
`ui/tray.py` needs `./dev restart` before it means anything, and so does
anything the tray decides at startup. The menu, wizard and chat are
launched fresh each time, so edits to those show up on their next launch
without a restart.

Nothing else is long-lived. `bin/dictate type` runs and exits, so CLI and
engine changes take effect on the next invocation.

Run a single surface straight out of the tree:

```bash
bin/dictate type          # a dictation, same as the hotkey
bin/dictate backend status
bin/dictate-menu          # radial menu
bin/dictate-setup         # setup wizard
bin/dictate-chat          # voice chat
```

## Only one dictatr at a time

Two trays on one session means two of everything: both bind the same
hotkeys, both draw a tray icon, and a keypress fires twice. It cannot
happen by accident, but it is worth knowing how it is prevented.

The tray holds the DBus name `io.github.rebreda.dictatr.tray`. A second
one asks whether that name is taken before claiming it, prints
`dictatr tray already running`, and exits 1. So `./dev restart` stops the
old one first, and re-running `install.sh` is harmless.

The one real way to end up with two is **installing the rpm or deb while
developing**: the package puts a tray in `/etc/xdg/autostart`, which
starts at login before your checkout does, and `/usr/bin/dictate` may win
on PATH. Develop from the checkout or from the package, not both.
`./dev doctor` checks for exactly this, along with the portal
registration and the launcher symlinks:

```
$ ./dev doctor
== a second dictatr ==
  ok: no tray outside this checkout
  ok: dictate -> /home/g/.local/bin/dictate
== launchers ==
  ok: all launchers linked
== desktop portal ==
  ok: app id registers (hotkeys and portal typing can work)
```

## When the keyboard goes strange after a dictation

If the desktop starts acting as though Ctrl or Shift is held, **press
and release that modifier on the real keyboard**. That resyncs the
compositor and is the only recovery worth trusting. Do not inject
release events for keys nobody pressed; an earlier version of this file
claimed that was harmless, which was never verified.

The cause and the fix are in [the guide](guide.md#typing-at-the-cursor):
portal typing now waits for the hotkey chord to be released before it
injects, because dictation is a toggle and the press that ends a
recording is still down when the transcript is ready. `runstate.CHORD`
carries that state from the tray to whichever process is delivering.

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
| `DICTATE_NO_PORTAL=1` | skip the portal typing tier outright |
| `DICTATE_TYPE_CMD=path` | hand transcripts to a command instead of typing them (the demo stage's seam) |
| `portal_typing = false` | same, from config, for a desktop where it misbehaves |
| `DICTATE_NO_SETUP=1` | stop the tray offering the setup wizard |
| `DICTATR_SETUP_STEP=N` | open the wizard straight on page N (0 to 3) |
| `RADIAL_DEMO=layout` | `python3 ui/radial.py` is a playground for the ring's geometry: item count, grouping, arc, depth |
| `RADIAL_DEMO=tether` | the same file shows the line that joins two surfaces during a handoff |
| `DICTATR_FROM=x,y,pid` | set by a surface handing over; the one arriving tethers back to that point and signals that pid |

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

`ui/setup.py` is three `Step` classes over one surface. A step's
`enter()` probes on a worker thread and reports back through
`GLib.idle_add`; the surface owns every widget and the steps only call
its `set_body`, `set_status`, `set_progress`, `set_extra` and
`set_items`. Nothing touches GTK off the main thread and nothing blocks
it, which matters because one step waits on a portal dialog and another
downloads a gigabyte.

The chrome is `ui/radial.py`, and the wizard is a layer-shell overlay
like the menu, not a dialog. Its Back and Close orbit the step badge on
a `Ring` in the card style — the same ring the menu is and the chat card
hangs off itself. The step's choices stay labelled pills rather than
bubbles: they are prose, and an icon plus a hover cannot carry "Set up
the built-in engine". The input region is clipped to the card and to the
ring's bubbles one at a time, so clicks elsewhere fall through.

`DICTATR_SETUP_STEP=N` opens straight on step N.

## Where things live

| Path | What is in it |
|---|---|
| `src/dictatr/` | the package: CLI, engine, delivery, archive, backend |
| `src/dictatr/backend/` | provider config, detection, managed lemond, client |
| `ui/radial_layout.py` | where the bubbles go: arcs, packing, grouping, depth. No GTK, so `tests/test_radial_layout.py` covers it |
| `ui/motion.py` | how anything moves: easing, `Track`/`Timeline`, the tether's outline. No GTK either; `tests/test_motion.py` |
| `ui/handoff.py` | one surface handing over to the next, with a tether across the gap |
| `ui/graph.py` | the scene as a graph, and the fractal zoom that moves through it. No GTK; `tests/test_graph.py` |
| `ui/scenes.py` | what each scene is, as nodes, and what its leaves do. No GTK at import; `tests/test_scenes.py` |
| `ui/canvas.py` | one widget that draws the whole scene: render nodes, a camera, and its own hit-testing |
| `ui/shell.py` | the resident process every drawn surface lives in, and the bus its shims call |
| `ui/radial.py` | the visual kit: palette, overlay, transitions, tether, icon theme — and the widget ring `ui/chat.py` still uses |
| `ui/menu.py` | the audio file picker, and the shell's surface in a process of its own |
| `ui/setup.py` | setup wizard |
| `ui/tray.py` | tray icon and the hotkey portal host |
| `ui/chat.py` | floating voice chat |
| `ui/portal_typed.py` | RemoteDesktop portal typing helper |
| `bin/` | the launcher shims |
| `dev` | the workflow script above |
| `packaging/` | `stage.sh`, the two builders, and `check.sh` (build + lint + install in a container) |
| `docs/demo/` | the capture harness (see its own README) |

A new surface is a scene, not a program: a handful of `graph.Node`
records in `ui/scenes.py`, drawn by the canvas in the resident shell.
Nodes name their children rather than owning them, so a node two scenes
both want is one node — which is what stops the surfaces from
reinventing each other. A leaf carries what choosing it does; see
`activate()` for the four kinds.

Anything that draws should paint with `ui/radial.py`: import the palette
and a `Style` rather than restating hex codes or pixel sizes, and call
`radial.apply_css()` so the bundled symbolic icons resolve. It should
never work out an angle or a radius for itself; that is what
`ui/radial_layout.py` is, and a second copy of it is how the surfaces
drifted apart the last time.

`ui/chat.py` is still a tree of widgets around a `radial.Ring`, and is
the only thing keeping that class alive. When it becomes a scene the
ring goes with it.

Nothing outside `ui/radial.py` should write a tick callback. `drive`,
`fade`, `grow`, `crossfade`, `scroll_to` and `play` are the animator, and
they settle instantly on a widget with no frame clock — motion is a way
of arriving somewhere, and somewhere is where it has to end up even when
nobody watches. That is also what lets the check tools drive these
surfaces without a window on screen.

The drawn side does not animate widgets at all: everything on the canvas
is a value on a `motion.Spring`, advanced once per frame in
`Canvas.tick`, and the canvas asks for frames only while something is
still moving. Anything you can grab or interrupt belongs on a spring —
a fixed-duration curve restarts from nothing when it is caught, which is
what makes an interface feel mechanical. `tools/shellcheck` steps the
canvas by hand, with no window, because the springs are solved
analytically rather than integrated.

Surfaces are separate processes, so handing over is a protocol rather
than a call: `handoff.leave` holds the old surface open and spawns the
new one knowing where the bubble you clicked was, `handoff.arrive` draws
the tether back to it and says when it has landed, and a 1.5s timeout
means a surface that never starts cannot strand the one that launched
it.

`tools/radialcheck`, `tools/chatcheck`, `tools/wizcheck` and
`tools/handoffcheck` build these surfaces without presenting them and
assert on the result, which is the only way to check a layer-shell
overlay on the machine you are working on. `tools/radialcheck --png
out.png` draws the layouts and the tether straight from the solver, with
no GTK and no display involved.

## Commits

Subjects follow [Conventional Commits](https://www.conventionalcommits.org):

```
type(scope): summary
```

`feat fix perf docs refactor test build ci chore revert`, an optional
lowercase scope (`chat menu tray wizard kit engine backend packaging
demo`), and a trailing `!` for a break. Under 72 characters.

```
feat(shake): open the chat by shaking the pointer
fix(packaging): stop vendoring the engine binary
fix(engine)!: drop the ydotool typing tier
```

Only the subject is constrained. The body is where this project says
*why*, at whatever length the change deserves — that has always been the
best part of the history and nothing here touches it.

The subject is constrained because it is read by machines: it becomes
the rpm `%changelog`, the Debian changelog and the release page. A
summary that is already one line about one change assembles into a
changelog; prose recalled days later turns into paragraphs nobody reads.

```bash
./dev hooks                              # check messages before they land
packaging/release-entry.py 0.5.0         # the next %changelog entry
```

`release-entry.py` reads the commits since the last tag and prints a
block to paste into `packaging/dictatr.spec`. It lists `feat`, `fix`,
`perf` and anything breaking; refactors, tests and CI work are real work
but not what a user reads a changelog for. The same rule file
(`.githooks/commit-msg`) is what CI applies, so the hook and the gate
cannot drift, and CI only checks the commits a push or PR adds — the
history predates the convention.

## Packages

```bash
packaging/check.sh fedora:latest # build + lint + install + smoke, in a container
packaging/stage.sh /tmp/tree     # exactly what a package installs
packaging/build-deb.sh           # -> dist/
packaging/build-rpm.sh           # builds from a git archive of HEAD
```

`check.sh` is what CI runs, one job per distro, so "it passed for me" and
"it passed in CI" mean the same thing. Reach for it before tagging.

`stage.sh` is the single source of truth for both builders, so a new file
that must ship gets added there once. It copies whole trees rather than
globbing one directory deep, which is how 0.3.0 shipped without
`src/dictatr/backend/` and would not import at all. More in
[packaging.md](packaging.md).

## Demo assets

`docs/demo/demo stills|video|voice|shell` regenerates the screenshots and
gifs on an isolated headless sway stage. It needs a handful of tools that
are not otherwise required; see [demo/README.md](demo/README.md). Nothing
else in the project depends on it.
