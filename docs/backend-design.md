# Backend and onboarding redesign

Status: proposal (2026-08-27), largely built. Where it disagrees
with the code, the code wins: ydotool and the uinput udev rule
were dropped, and typing is portal-then-clipboard. Grounded in three research passes: how
Lemonade ships today, how comparable local-AI apps manage runtimes, and
current Linux conventions for permissions and first-run setup. Sources
inline.

## What changed under us

1. **Lemonade is now a C++ daemon with an official embeddable build.**
   `lemonade-embeddable-X.Y.Z-<os>.tar.gz` is ~7 MB, contains `lemond`
   plus resource catalogs, and the docs recommend exactly the
   run-as-subprocess pattern (`lemond <dir> --port N`). Full REST model
   management: `POST /v1/pull` with SSE progress, `/v1/delete`,
   `/internal/pin`, `GET /v1/system-info` for hardware-aware backend
   selection (whisper.cpp cpu/vulkan/rocm variants downloaded on demand,
   sha256-pinned). Clean stop via `POST /internal/shutdown`. Multiple
   instances are supported (own dirs + port, `--no-broadcast`).
   Apache-2.0; redistribution is the intended model. Default port is
   now 13305, not 8080. Weekly releases; pin and vendor one version.
2. **The ecosystem pattern is app-managed runtimes, not "go install
   it".** LM Studio ships separately-versioned runtime packages; Jan
   downloads its llama.cpp engine; Alpaca tried static bundling, then
   Flatpak extensions, and settled on an in-app manager that installs,
   spawns, and updates Ollama as a managed child. Endpoint
   configurability convention: a connections list of base URL + API key
   (+ model), honoring `OPENAI_BASE_URL`/`OPENAI_API_KEY`, with
   **explicit per-capability endpoints** (separate STT/TTS/embeddings
   config, as in Open WebUI) instead of runtime capability probing.
3. **Portals ended the sudo era on modern desktops.** GlobalShortcuts
   portal (KDE mature, GNOME >= 48) binds hotkeys with a native consent
   dialog, `preferred_trigger` prefills, press+release events, and
   grants that persist. RemoteDesktop portal v2 (`persist_mode: 2` +
   `restore_token`) allows keyboard injection after one dialog, tokens
   surviving reboots on Plasma >= 6.1.1 and GNOME >= 46. Writing
   `kglobalshortcutsrc` and running a ydotool daemon become fallbacks,
   not the design. For the uinput fallback tier, the accepted no-script
   pattern is a polkit action + tiny privileged helper (input-remapper
   model). First-run wizards are HIG-sanctioned when settings genuinely
   block first use (OBS/Kdenlive shape: short, probing, skippable,
   re-runnable).

## Design

### 1. Backend layer (`src/dictatr/backend/`)

One provider abstraction behind the existing single seam (every server
call already flows through `settings.whisper.api_base`). Three provider
kinds, selected in config with auto-detection:

- **managed** (default): dictatr's own `lemond`, vendored in the
  package (`/usr/lib/dictatr/lemond/`, +7 MB) and downloaded on demand
  for source installs (sha256-pinned release asset into
  `~/.local/share/dictatr/lemond/`). Private instance: own state dir
  (`~/.local/share/dictatr/lemonade/`), own port, `--no-broadcast`,
  API key generated per install. Models go to the standard HF cache
  (`models_dir: auto`) so they are shared with other local-AI apps.
  Lifecycle: a `dictatr-lemond` user unit owns the process; the app
  starts it on demand and health-checks `/live`. Model pulls and
  backend installs run through the REST API with progress surfaced in
  the UI. The ASR model is pinned via `/internal/pin`.
- **system**: an existing Lemonade (detected on 13305, legacy 8080, or
  configured URL). Zero management; dictatr is a pure client. This is
  today's behavior, preserved.
- **custom**: OpenAI-compatible endpoints, configured per capability
  (the researched convention): `asr`, `chat`, `tts`, `embeddings`, each
  base URL + API key + model. `OPENAI_BASE_URL`/`OPENAI_API_KEY` are
  honored. Realtime streaming ASR is treated as present only for
  Lemonade-protocol servers; a custom ASR endpoint without `/realtime`
  degrades to push-to-stop batch transcription (record until the
  hotkey fires again, then one `audio/transcriptions` call). Features
  whose endpoint is unconfigured switch off cleanly (no ask mode
  without a chat endpoint, and so on).

`backend/client.py` exposes the typed surface the rest of the app uses
(realtime session URL, transcribe, chat, speak, embed, models) and adds
Bearer auth everywhere. `engine.py`, `batch.py`, `llm.py`, `recall.py`,
and the settings UI stop touching `api_base` directly.

### 2. Desktop integration (`src/dictatr/desktop/`)

- **Hotkeys move into the tray process** via the GlobalShortcuts
  portal: the tray (already resident and autostarted) creates the
  session, binds the four actions with `preferred_trigger` prefills,
  and reacts to `Activated`/`Deactivated` by driving dictation. On KDE
  the bindings appear in System Settings natively; on GNOME 48+ one
  consent dialog. Press+release events make push-to-talk possible
  later. `dictate-hotkeys` (kglobalshortcutsrc) remains only as a
  fallback for environments without the portal; wlroots users bind the
  CLI commands themselves as they do today.
- **Typing ladder**: RemoteDesktop portal (`NotifyKeyboardKeysym`,
  `persist_mode: 2`, token stored in the runstate dir; one dialog ever
  on Plasma >= 6.1.1 / GNOME >= 46) -> ydotool (uinput; packaged udev
  rule + user daemon, or polkit-installed rule on source installs) ->
  clipboard. The tray shows which tier is active; the delivery outcome
  notification stays truthful.

### 3. Onboarding (`ui/setup.py`)

A short, skippable, re-runnable wizard in the OBS/Kdenlive shape, shown
by the tray when setup is incomplete and available forever as
`dictatr setup` and a tray menu item. Each page is a probe plus one
action button; every page doubles as a permanent status row for
diagnostics:

1. **Engine**: probe for a running Lemonade (13305, 8080, configured
   URL). Found: use it. Not found: "Use the built-in engine" starts
   managed `lemond` and pulls Moonshine with a progress bar (~1 GB;
   size shown first), or "Custom endpoint" opens the per-capability
   form. Optional models (ask, TTS, recall) are checkboxes with sizes.
2. **Typing**: button fires the RemoteDesktop portal dialog (with
   persistence), then types a test string into a box in the window.
   Environments without the portal get the fallback path (enable the
   ydotool user service; polkit prompt to install the udev rule on
   source installs).
3. **Hotkeys**: button fires the GlobalShortcuts bind dialog, prefilled
   with the defaults; shows the resulting bindings.
4. **Try it**: one live dictation into a text box, using whatever the
   previous steps configured. Green check means the whole chain works.

The packages then install nothing user-visible beyond the tray
autostart; there are no post-install shell instructions.

## Migration and phases

Compatibility: `LEMONADE_URL` keeps working (implies the system
provider); config gains a `[backend]` table; detection probes both the
new and legacy ports. The realtime protocol is unchanged (OpenAI
Realtime events), so `engine.py` keeps its session logic.

1. **Backend layer**: provider abstraction, managed-lemond lifecycle,
   REST model management, `dictatr backend status|start|pull` CLI. No
   UI changes yet.
2. **Portals**: typing ladder with the RemoteDesktop portal; hotkeys
   via GlobalShortcuts hosted in the tray.
3. **Onboarding wizard** plus packaging updates (vendor the embeddable
   tarball, CI fetches the pinned release asset by checksum).
4. **Retirements**: dictate-hotkeys demoted to fallback, ydotoold
   docs trimmed, guide/README rewritten around the wizard.
