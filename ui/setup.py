#!/usr/bin/env python3
"""First-run setup wizard for dictatr, in the radial visual language.

Four pages, each a probe plus one action: engine, typing, hotkeys, and a
live dictation to prove the chain works. Short, skippable, re-runnable
(`dictate setup`), so the packages never have to print shell
instructions after install.

The chrome is the radial kit (ui/radial.py): the page emblem is a
ProgressBubble at the center of a small orbit of step bubbles, and the
orbit rotates so the current step is always at the top. Moving forward
turns it clockwise, going back unwinds it, and the text block slides in
from the side it came from.

Nothing blocks the GTK loop: every probe, download and portal dance runs
on a worker thread and reports back through GLib.idle_add.
"""

import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DICTATE = str(REPO / "bin" / "dictate")
PORTAL_HELPER = str(REPO / "ui" / "portal_typed.py")
# The helper needs PyGObject, and so did we: reuse this interpreter
# rather than whatever "python3" means on the caller's PATH.
PYTHON = sys.executable or "python3"
sys.path.insert(0, str(REPO / "src"))
from dictatr import deliver  # noqa: E402
from dictatr.settings import settings, write_config  # noqa: E402

sys.path.insert(0, str(REPO / "ui"))
import portal_typed  # noqa: E402  (pure helpers only; its gi imports are lazy)
import radial  # noqa: E402
from radial import BLUE, CHECK_ICON, GREEN, INK, ProgressBubble  # noqa: E402

APP_ID = "io.github.rebreda.dictatr"
PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
GS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
TRAY_BUS = "io.github.rebreda.dictatr.tray"
TEST_PHRASE = "dictatr can type here"

# Same four actions the tray binds; see PORTAL_SHORTCUTS in ui/tray.py.
SHORTCUTS = [
    ("dictate", "Dictate at cursor", "CTRL+ALT+d"),
    ("menu", "Open the dictate menu", "CTRL+ALT+space"),
    ("cancel", "Cancel dictation", "CTRL+ALT+c"),
    ("listen", "Toggle always-on capture", "CTRL+ALT+a"),
]

# The orbit is the menu's ring, same box and same radius, so the two
# read as one shape doing two jobs. Only the satellites differ: step
# markers are indicators, not targets, so they are smaller.
RING_SIZE = radial.SIZE
EMBLEM = radial.CENTER_BUBBLE
ORBIT = radial.RADIUS
STEP_BUBBLE = 30
ROTATE_S = 0.42     # step-to-step orbit rotation
SLIDE_S = 0.26      # text block crossfade
SHIFT = 22          # how far the page slides, and its resting margin

SETUP_CSS = f"""
window.setup {{ background: #1b1c21; }}
.setup-page {{ padding: 4px 16px 26px 16px; }}
.step {{
  border-radius: 9999px;
  border: 1px solid alpha(#ffffff, 0.10);
  background: alpha(#1c1d22, 0.93);
  transition: background 160ms ease, border-color 160ms ease;
}}
.step image {{ color: alpha({INK}, 0.55); }}
.step.active {{
  background: alpha({BLUE}, 0.28); border-color: alpha({BLUE}, 0.65);
}}
.step.active image {{ color: {BLUE}; }}
.step.done {{
  background: alpha({GREEN}, 0.22); border-color: alpha({GREEN}, 0.55);
}}
.step.done image {{ color: {GREEN}; }}
/* the emblem sits on a flat dark page, not a wallpaper: lift it off */
.emblem {{
  background: alpha(#ffffff, 0.055);
  border-color: alpha(#ffffff, 0.16);
}}
.emblem image {{ color: {BLUE}; }}
.title {{ font-size: 19px; font-weight: 700; color: {INK}; }}
.body {{ color: alpha({INK}, 0.72); }}
.status {{ font-size: 13px; color: alpha({INK}, 0.60); }}
.status.good {{ color: {GREEN}; }}
.status.bad {{ color: #f28b82; }}
.setup entry {{
  background: alpha(#ffffff, 0.06); color: {INK};
  border: 1px solid alpha(#ffffff, 0.12); border-radius: 8px;
  padding: 8px 10px;
}}
.setup entry:focus-within {{ border-color: alpha({BLUE}, 0.7); }}
.setup button {{
  border-radius: 8px; padding: 7px 15px; color: {INK};
  background: alpha(#ffffff, 0.07);
  border: 1px solid alpha(#ffffff, 0.10);
}}
.setup button:hover {{ background: alpha(#ffffff, 0.13); }}
.setup button.primary {{
  background: alpha({BLUE}, 0.26); border-color: alpha({BLUE}, 0.55);
}}
.setup button.primary:hover {{ background: alpha({BLUE}, 0.38); }}
.setup button:disabled {{ color: alpha({INK}, 0.35); }}
.setup button.flat {{ background: transparent; border-color: transparent; }}
.setup button.flat:hover {{ background: alpha(#ffffff, 0.08); }}
""".encode()


def _run(cmd, timeout=20, **kw):
    """Subprocess helper: never raises, always returns a CompletedProcess."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              check=False, timeout=timeout, **kw)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(cmd, 1, "", "not available")


def _in_background(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True).start()


def _name_has_owner(name: str) -> bool:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (name,)), None,
            Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    except GLib.Error:
        return False


def _portal_version(iface: str):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return bus.call_sync(
            PORTAL_BUS, PORTAL_PATH, "org.freedesktop.DBus.Properties",
            "Get", GLib.Variant("(ss)", (iface, "version")), None,
            Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    except GLib.Error:
        return None


# --- the orbit ---------------------------------------------------------

class StepRing(Gtk.Fixed):
    """The wizard's progress indicator, built from the menu's vocabulary:
    a big emblem bubble with the step's icon, orbited by one small bubble
    per step. The orbit rotates to bring the current step to the top, so
    forward and back read as the same motion in opposite directions."""

    def __init__(self, icons):
        super().__init__()
        self.set_size_request(RING_SIZE, RING_SIZE)
        self._c = RING_SIZE / 2
        self._n = len(icons)
        self._rot = 0.0

        self.emblem = ProgressBubble(icons[0], diameter=EMBLEM)
        self.emblem.set_icon_size(24)          # the page's focal point
        self.emblem.inner.add_css_class("emblem")
        side = EMBLEM + 2 * ProgressBubble.ARC_PAD
        self.put(self.emblem, self._c - side / 2, self._c - side / 2)

        self.steps = []
        for icon in icons:
            b = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            b.add_css_class("step")
            b.set_size_request(STEP_BUBBLE, STEP_BUBBLE)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(15)
            img.set_hexpand(True)
            b.append(img)
            self.put(b, 0, 0)
            self.steps.append((b, img, icon))
        self._layout()

    def _layout(self):
        span = 2 * math.pi / self._n
        for i, (b, _img, _icon) in enumerate(self.steps):
            a = -math.pi / 2 + (i - self._rot) * span
            self.move(b, self._c + ORBIT * math.cos(a) - STEP_BUBBLE / 2,
                      self._c + ORBIT * math.sin(a) - STEP_BUBBLE / 2)

    def show_step(self, index, done_upto, icon, animate=True):
        for i, (b, img, own) in enumerate(self.steps):
            b.remove_css_class("active")
            b.remove_css_class("done")
            if i < done_upto:
                b.add_css_class("done")
                img.set_from_icon_name(CHECK_ICON)
            else:
                img.set_from_icon_name(own)
                if i == index:
                    b.add_css_class("active")
        self.emblem.set_icon(icon)
        self.emblem.set_fraction(0.0)
        start, target = self._rot, float(index)
        if not animate or abs(target - start) < 1e-6:
            self._rot = target
            self._layout()
            return

        t0 = [None]

        def tick(_w, clock):
            now = clock.get_frame_time() / 1e6
            if t0[0] is None:
                t0[0] = now
            p = min((now - t0[0]) / ROTATE_S, 1.0)
            self._rot = start + (target - start) * (1 - (1 - p) ** 3)
            self._layout()
            return p < 1.0

        self.add_tick_callback(tick)


# --- pages -------------------------------------------------------------

class Step:
    """One wizard page. enter() probes on a worker thread and calls the
    wizard's ui.* setters; the wizard owns every widget."""

    key = ""
    icon = ""
    title = ""

    def __init__(self, wiz):
        self.wiz = wiz

    def enter(self):
        raise NotImplementedError


class EngineStep(Step):
    key, icon = "engine", "dictatr-engine-symbolic"
    title = "Inference engine"

    def enter(self):
        w = self.wiz
        w.set_body("dictatr transcribes on this machine through a Lemonade "
                   "server. It can run one for you, or use one you already "
                   "have.")
        w.set_status("Looking for a server…")
        w.busy(True)
        _in_background(self._probe)

    def _probe(self):
        from dictatr.backend import client, detect, lemond
        found = None
        try:
            if lemond.alive():
                found = ("managed", lemond.api_base())
            elif base := detect.detect():
                found = ("system", base)
            else:
                b = client.resolve(allow_start=False)
                if b.kind == "custom":
                    found = ("custom", b.api_base)
        except Exception as e:            # never let a probe kill the page
            GLib.idle_add(self._probe_failed, str(e))
            return
        GLib.idle_add(self._probed, found, lemond.status())

    def _probe_failed(self, msg):
        w = self.wiz
        w.busy(False)
        w.set_status(f"Could not probe: {msg}", "bad")
        w.set_actions(primary=("Set up the built-in engine", self._install))

    def _probed(self, found, managed):
        w = self.wiz
        w.busy(False)
        if found:
            kind, base = found
            names = {"managed": "The built-in engine is running",
                     "system": "Found a Lemonade server",
                     "custom": "Using your configured endpoint"}
            w.set_status(f"{names[kind]} at {base}", "good")
            w.set_actions(
                primary=("Continue", lambda: self._keep(kind, base)),
                secondary=("Use something else", self._show_custom))
            return
        have = "installed" if managed["binary"] else "a 7 MB download"
        w.set_status(f"No server running. The built-in engine is {have}; "
                     "the speech model is about 1 GB, downloaded once.")
        w.set_actions(primary=("Set up the built-in engine", self._install),
                      secondary=("Use a custom endpoint", self._show_custom))

    def _keep(self, kind, base):
        cfg = {"backend": kind}
        if kind == "system":
            cfg["backend_url"] = base
        write_config(cfg)
        self.wiz.advance()

    # --- managed install ------------------------------------------------
    def _install(self):
        w = self.wiz
        w.busy(True)
        w.set_actions()
        w.set_status("Preparing the built-in engine…")
        _in_background(self._install_worker)

    def _install_worker(self):
        from dictatr.backend import lemond
        try:
            if not (lemond.VENDORED.is_file() or lemond.DOWNLOADED.is_file()):
                GLib.idle_add(self.wiz.set_status, "Downloading the engine…")
                lemond.download_lemond(
                    lambda pct: GLib.idle_add(self.wiz.set_progress,
                                              pct / 100))
            GLib.idle_add(self.wiz.set_progress, None)
            GLib.idle_add(self.wiz.set_status, "Starting the engine…")
            lemond.start()
            write_config({"backend": "managed"})
            model = settings.whisper.model
            GLib.idle_add(self.wiz.set_status, f"Downloading {model}…")

            def on_event(ev):
                pct = ev.get("percent")
                if pct is not None:
                    GLib.idle_add(self.wiz.set_progress, float(pct) / 100)

            lemond.pull(model, on_progress=on_event)
            lemond.pin(model)
        except Exception as e:
            GLib.idle_add(self._install_failed, str(e))
            return
        GLib.idle_add(self._installed)

    def _install_failed(self, msg):
        w = self.wiz
        w.busy(False)
        w.set_progress(None)
        w.set_status(f"Setup failed: {msg}", "bad")
        w.set_actions(primary=("Try again", self._install),
                      secondary=("Use a custom endpoint", self._show_custom))

    def _installed(self):
        w = self.wiz
        w.busy(False)
        w.set_progress(1.0)
        w.set_status("The built-in engine is ready", "good")
        w.set_actions(primary=("Continue", w.advance))

    # --- custom endpoint form -------------------------------------------
    def _show_custom(self):
        w = self.wiz
        from dictatr.backend import config as bconfig
        cfg = bconfig.load()
        url = Gtk.Entry(placeholder_text="https://host:port/api/v1",
                        text=(cfg.url or ""), hexpand=True)
        key = Gtk.Entry(placeholder_text="API key (optional)",
                        text=(cfg.caps["asr"]["key"] or ""), hexpand=True,
                        visibility=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(url)
        box.append(key)
        w.set_extra(box)
        w.set_status("Any OpenAI-compatible server. Streaming dictation "
                     "needs Lemonade's /realtime; others transcribe when "
                     "you stop.")
        w.set_actions(primary=("Test and save",
                               lambda: self._test_custom(url, key)),
                      secondary=("Back to the built-in engine", self.enter))

    def _test_custom(self, url_entry, key_entry):
        base = url_entry.get_text().strip().rstrip("/")
        key = key_entry.get_text().strip()
        if not base:
            self.wiz.set_status("Enter a base URL first", "bad")
            return
        self.wiz.busy(True)
        self.wiz.set_status("Contacting the server…")
        _in_background(self._test_worker, base, key)

    def _test_worker(self, base, key):
        import json
        import urllib.request
        req = urllib.request.Request(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}"} if key else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                models = [m["id"] for m in json.load(r).get("data", [])]
        except Exception as e:
            GLib.idle_add(self.wiz.busy, False)
            GLib.idle_add(self.wiz.set_status, f"No answer: {e}", "bad")
            return
        write_config({"backend": "custom", "backend_url": base,
                      "asr_url": base, "asr_key": key or None,
                      "chat_url": base, "chat_key": key or None})
        GLib.idle_add(self._tested, len(models))

    def _tested(self, n):
        w = self.wiz
        w.busy(False)
        w.set_status(f"Connected: {n} model(s) available", "good")
        w.set_actions(primary=("Continue", w.advance))


class TypingStep(Step):
    key, icon = "typing", "dictatr-typing-symbolic"
    title = "Typing at the cursor"

    def enter(self):
        w = self.wiz
        w.set_body("To drop text straight into whatever you are writing in, "
                   "dictatr needs one permission from your desktop. Without "
                   "it, transcripts go to the clipboard instead.")
        w.set_status("Checking…")
        w.busy(True)
        _in_background(self._probe)

    def _probe(self):
        # --check exits 0 only when the portal can inject keyboard events.
        portal = _run([PYTHON, PORTAL_HELPER, "--check"]).returncode == 0
        granted = bool(portal_typed.load_token())
        ydotool = bool(shutil.which("ydotool"))
        service = _run(["systemctl", "--user", "is-active",
                        "dictatr-ydotoold.service"], timeout=8)
        GLib.idle_add(self._probed, portal, granted, ydotool,
                      (service.stdout or "").strip() == "active")

    def _probed(self, portal, granted, ydotool, service_up):
        w = self.wiz
        w.busy(False)
        w.set_extra(self._test_entry())
        if granted:
            w.set_status("Typing is already allowed on this desktop", "good")
            w.set_actions(primary=("Test it", self._test),
                          secondary=("Skip", w.advance))
        elif portal:
            w.set_status("Your desktop can grant this. The dialog appears "
                         "once and the permission is remembered.")
            w.set_actions(primary=("Allow typing", self._grant),
                          secondary=("Skip, use the clipboard", w.advance))
        elif ydotool:
            if service_up:
                w.set_status("No typing portal here, but the ydotool service "
                             "is running", "good")
                w.set_actions(primary=("Test it", self._test),
                              secondary=("Skip", w.advance))
            else:
                w.set_status("No typing portal on this desktop. dictatr can "
                             "use ydotool instead, as your own user.")
                w.set_actions(primary=("Enable the typing service",
                                       self._enable_ydotoold),
                              secondary=("Skip, use the clipboard", w.advance))
        else:
            w.set_status("Nothing here can type at the cursor. Transcripts "
                         "will go to the clipboard, which always works.",
                         "bad")
            w.set_actions(primary=("Continue", w.advance))

    def _test_entry(self):
        self.entry = Gtk.Entry(placeholder_text="the test types in here",
                               hexpand=True)
        return self.entry

    def _grant(self):
        w = self.wiz
        w.busy(True)
        w.set_status("Waiting for your desktop's permission dialog…")
        _in_background(self._grant_worker)

    def _grant_worker(self):
        r = _run([PYTHON, PORTAL_HELPER, "--grant"], timeout=150)
        GLib.idle_add(self._granted, r.returncode == 0,
                      (r.stderr or r.stdout or "").strip())

    def _granted(self, ok, msg):
        w = self.wiz
        w.busy(False)
        if ok:
            w.set_status("Permission granted", "good")
            self._test()
        else:
            w.set_status(msg or "The request was refused", "bad")
            w.set_actions(primary=("Try again", self._grant),
                          secondary=("Skip, use the clipboard", w.advance))

    def _enable_ydotoold(self):
        w = self.wiz
        w.busy(True)
        w.set_status("Starting the typing service…")
        _in_background(self._enable_worker)

    def _enable_worker(self):
        r = _run(["systemctl", "--user", "enable", "--now",
                  "dictatr-ydotoold.service"], timeout=30)
        if r.returncode == 0:
            time.sleep(0.6)   # let the daemon create its socket
        GLib.idle_add(self._enabled, r.returncode == 0,
                      (r.stderr or "").strip())

    def _enabled(self, ok, msg):
        w = self.wiz
        w.busy(False)
        if ok:
            w.set_status("Typing service running", "good")
            self._test()
        else:
            w.set_status(msg or "Could not start the service", "bad")
            w.set_actions(primary=("Continue anyway", w.advance))

    # --- the live test ---------------------------------------------------
    def _test(self):
        w = self.wiz
        self.entry.set_text("")
        self.entry.grab_focus()
        w.busy(True)
        w.set_status("Typing a test line…")
        w.set_actions()
        _in_background(self._test_worker)

    def _test_worker(self):
        time.sleep(0.4)      # let the focus land before keys arrive
        tier = deliver.type_text(TEST_PHRASE)
        GLib.idle_add(self._tested, tier)

    def _tested(self, tier):
        w = self.wiz
        w.busy(False)
        landed = self.entry.get_text().strip() == TEST_PHRASE
        if landed:
            w.set_status(f"Typing works ({tier})", "good")
            w.set_actions(primary=("Continue", w.advance))
        elif tier:
            w.set_status(f"Sent through {tier}, but nothing arrived in the "
                         "box. Keep this window focused and try again.",
                         "bad")
            w.set_actions(primary=("Try again", self._test),
                          secondary=("Continue", w.advance))
        else:
            w.set_status("Nothing could type. dictatr will use the "
                         "clipboard.", "bad")
            w.set_actions(primary=("Continue", w.advance))


class HotkeysStep(Step):
    key, icon = "hotkeys", "dictatr-hotkey-symbolic"
    title = "Hotkeys"

    def enter(self):
        w = self.wiz
        w.set_body("Dictation is a keypress. dictatr asks your desktop to "
                   "reserve four shortcuts; you can change them later in "
                   "your desktop's shortcut settings.")
        w.set_status("Checking…")
        w.busy(True)
        _in_background(self._probe)

    def _probe(self):
        version = _portal_version(GS_IFACE)
        kde = bool(shutil.which("kwriteconfig6"))
        GLib.idle_add(self._probed, version, kde)

    def _probed(self, version, kde):
        w = self.wiz
        w.busy(False)
        w.set_extra(self._table())
        if version is not None:
            w.set_status("Your desktop can bind these. One dialog, then "
                         "they stay bound.")
            w.set_actions(primary=("Bind the shortcuts", self._bind),
                          secondary=("Skip", w.advance))
        elif kde:
            w.set_status("No shortcuts portal here. dictatr can write them "
                         "into the KDE config instead (active after you log "
                         "back in).")
            w.set_actions(primary=("Write KDE shortcuts", self._legacy),
                          secondary=("Skip", w.advance))
        else:
            w.set_status("This desktop has no shortcuts portal. Bind the "
                         "commands below by hand in its settings.", "bad")
            w.set_actions(primary=("Continue", w.advance))

    def _table(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.rows = {}
        for sid, desc, trigger in SHORTCUTS:
            row = Gtk.Box(spacing=10)
            left = Gtk.Label(label=desc, xalign=0, hexpand=True)
            left.add_css_class("status")
            right = Gtk.Label(label=trigger.replace("CTRL", "Ctrl")
                              .replace("ALT", "Alt").replace("+", " + "),
                              xalign=1)
            right.add_css_class("status")
            row.append(left)
            row.append(right)
            box.append(row)
            self.rows[sid] = right
        return box

    def _bind(self):
        w = self.wiz
        w.busy(True)
        w.set_status("Waiting for your desktop's shortcut dialog…")
        w.set_actions()
        Binder(self._bound).run()

    def _bound(self, ok, results, msg):
        w = self.wiz
        w.busy(False)
        if not ok:
            w.set_status(msg or "The request was refused", "bad")
            w.set_actions(primary=("Try again", self._bind),
                          secondary=("Skip", w.advance))
            return
        for sid, meta in results:
            if sid in self.rows:
                self.rows[sid].set_label(
                    meta.get("trigger_description") or "bound")
        self._ensure_tray()
        w.set_status("Shortcuts bound", "good")
        w.set_actions(primary=("Continue", w.advance))

    def _ensure_tray(self):
        """The tray owns the live shortcut session; if it is not up, the
        keys would bind to nothing after this window closes."""
        if not _name_has_owner(TRAY_BUS):
            try:
                subprocess.Popen([str(REPO / "bin" / "dictate-tray")],
                                 start_new_session=True,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                pass

    def _legacy(self):
        w = self.wiz
        w.busy(True)
        _in_background(self._legacy_worker)

    def _legacy_worker(self):
        r = _run([str(REPO / "bin" / "dictate-hotkeys")], timeout=30)
        GLib.idle_add(self._legacy_done, r.returncode == 0)

    def _legacy_done(self, ok):
        w = self.wiz
        w.busy(False)
        self._ensure_tray()
        if ok:
            w.set_status("Written. They start working after you log out and "
                         "back in.", "good")
        else:
            w.set_status("Could not write the KDE config", "bad")
        w.set_actions(primary=("Continue", w.advance))


class TryStep(Step):
    key, icon = "try", "dictatr-mic-symbolic"
    title = "Try it"

    def enter(self):
        w = self.wiz
        w.set_body("One real dictation, using everything you just set up. "
                   "Press the button, say a sentence, then stop talking.")
        self.entry = Gtk.Entry(placeholder_text="your words land here",
                               hexpand=True)
        w.set_extra(self.entry)
        w.set_status("Ready when you are")
        w.set_actions(primary=("Start dictation", self._go),
                      secondary=("Finish", self._finish))

    def _go(self):
        w = self.wiz
        self.entry.set_text("")
        self.entry.grab_focus()
        w.busy(True)
        w.set_status("Listening. Say something, then pause.")
        w.set_actions()
        _in_background(self._worker)

    def _worker(self):
        r = _run([DICTATE, "type"], timeout=120)
        GLib.idle_add(self._done, r.returncode == 0,
                      (r.stderr or "").strip().splitlines()[-1:] or [""])

    def _done(self, ok, tail):
        w = self.wiz
        w.busy(False)
        text = self.entry.get_text().strip()
        if text:
            w.set_status(f"Heard: {text}", "good")
            w.set_actions(primary=("Finish", self._finish),
                          secondary=("Again", self._go))
        elif ok:
            w.set_status("Transcribed, but the text did not land here. It is "
                         "on the clipboard: press Ctrl+V in the box.", "bad")
            w.set_actions(primary=("Finish", self._finish),
                          secondary=("Again", self._go))
        else:
            w.set_status(tail[0] or "Dictation failed. Check the engine "
                                    "step.", "bad")
            w.set_actions(primary=("Try again", self._go),
                          secondary=("Finish", self._finish))

    def _finish(self):
        write_config({"setup_done": True})
        self.wiz.mark_complete()
        self.wiz.close()


class Binder:
    """One GlobalShortcuts bind dance: CreateSession, BindShortcuts, read
    back the triggers the desktop actually assigned, then close the
    session (the tray hosts the live one). Async so the dialog does not
    freeze the window."""

    def __init__(self, done):
        self.done = done
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._sender = self.bus.get_unique_name().lstrip(":").replace(".", "_")
        self._n = 0
        self.session = None

    def run(self):
        try:
            self.bus.call_sync(
                PORTAL_BUS, PORTAL_PATH,
                "org.freedesktop.host.portal.Registry", "Register",
                GLib.Variant("(sa{sv})", (APP_ID, {})), None,
                Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            pass
        self._request("CreateSession", "(a{sv})", (),
                      {"session_handle_token": GLib.Variant("s", "dictatrsetup")},
                      self._on_session)

    def _request(self, method, sig, args, options, cb, timeout_s=180):
        self._n += 1
        token = f"dictatrsetup{os.getpid()}_{self._n}"
        req = (f"/org/freedesktop/portal/desktop/request/"
               f"{self._sender}/{token}")
        state = {"fired": False}

        def on_response(_bus, _s, _p, _i, _sg, params):
            if state["fired"]:
                return
            state["fired"] = True
            self.bus.signal_unsubscribe(sub)
            code, results = params.unpack()
            cb(code, results)

        sub = self.bus.signal_subscribe(
            PORTAL_BUS, "org.freedesktop.portal.Request", "Response", req,
            None, Gio.DBusSignalFlags.NONE, on_response)
        opts = dict(options, handle_token=GLib.Variant("s", token))
        try:
            self.bus.call_sync(PORTAL_BUS, PORTAL_PATH, GS_IFACE, method,
                               GLib.Variant(sig, tuple(args) + (opts,)),
                               None, Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as e:
            self.bus.signal_unsubscribe(sub)
            self.done(False, [], e.message)
            return

        def expire():
            if not state["fired"]:
                state["fired"] = True
                self.bus.signal_unsubscribe(sub)
                self.done(False, [], f"no answer within {timeout_s}s")
            return False

        GLib.timeout_add_seconds(timeout_s, expire)

    def _on_session(self, code, results):
        self.session = results.get("session_handle")
        if code or not self.session:
            self.done(False, [], f"the session was refused ({code})")
            return
        shorts = [(sid, {"description": GLib.Variant("s", desc),
                         "preferred_trigger": GLib.Variant("s", trig)})
                  for sid, desc, trig in SHORTCUTS]
        self._request("BindShortcuts", "(oa(sa{sv})sa{sv})",
                      (self.session, shorts, ""), {}, self._on_bound)

    def _on_bound(self, code, results):
        self._close()
        if code:
            self.done(False, [], f"the request was refused ({code})")
            return
        self.done(True, results.get("shortcuts") or [], "")

    def _close(self):
        if not self.session:
            return
        try:
            self.bus.call_sync(PORTAL_BUS, self.session,
                               "org.freedesktop.portal.Session", "Close",
                               None, None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            pass


# --- the window --------------------------------------------------------

class Wizard(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Set up dictatr")
        radial.apply_css(SETUP_CSS)
        self.add_css_class("setup")
        self.set_default_size(560, 620)

        self.steps = [EngineStep(self), TypingStep(self), HotkeysStep(self),
                      TryStep(self)]
        self.index = 0
        self.reached = 0
        self.completed = False
        self._fraction = 0.0

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.add_css_class("setup-page")
        self.set_child(outer)

        self.ring = StepRing([s.icon for s in self.steps])
        self.ring.set_halign(Gtk.Align.CENTER)
        self.ring.set_margin_top(22)
        self.ring.set_margin_bottom(14)
        outer.append(self.ring)

        # vexpand claims the leftover height so the buttons stay pinned
        # to the bottom; valign START keeps the text under the ring
        # instead of drifting into the middle of the gap.
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            vexpand=True, valign=Gtk.Align.START)
        self.page.set_margin_top(10)
        self.page.set_margin_start(SHIFT)
        self.page.set_margin_end(SHIFT)
        outer.append(self.page)

        self.title = Gtk.Label(xalign=0.5)
        self.title.add_css_class("title")
        self.body = Gtk.Label(xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
        self.body.add_css_class("body")
        self.status = Gtk.Label(xalign=0.5, wrap=True,
                                justify=Gtk.Justification.CENTER)
        self.status.add_css_class("status")
        self.extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.extra.set_margin_top(4)
        for w in (self.title, self.body, self.extra, self.status):
            self.page.append(w)

        self.buttons = Gtk.Box(spacing=10, halign=Gtk.Align.FILL)
        self.buttons.set_margin_top(16)
        self.back_btn = Gtk.Button(label="Back")
        self.back_btn.add_css_class("flat")
        self.back_btn.connect("clicked", lambda _b: self.back())
        self.secondary_btn = Gtk.Button()
        self.primary_btn = Gtk.Button()
        self.primary_btn.add_css_class("primary")
        gap = Gtk.Box(hexpand=True)
        for w in (self.back_btn, gap, self.secondary_btn, self.primary_btn):
            self.buttons.append(w)
        outer.append(self.buttons)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)

        # DICTATR_SETUP_STEP opens straight on one page: for capture and
        # for eyeballing a page without walking the whole wizard.
        try:
            start = max(0, min(int(os.environ.get("DICTATR_SETUP_STEP", 0)),
                               len(self.steps) - 1))
        except ValueError:
            start = 0
        self._show(start, animate=False)

    # --- ui setters used by the steps ------------------------------------
    def set_body(self, text):
        self.body.set_label(text)

    def set_status(self, text, tone=""):
        self.status.remove_css_class("good")
        self.status.remove_css_class("bad")
        if tone:
            self.status.add_css_class(tone)
        self.status.set_label(text)

    def set_progress(self, fraction):
        """A determinate arc (0..1), or None to clear it. Wins over the
        busy spinner: a download knows more than "working"."""
        self._fraction = 0.0 if fraction is None else fraction
        self.ring.emblem.set_fraction(self._fraction)

    def busy(self, on):
        """Spin the emblem's arc while a worker runs. Stopping restores
        whatever determinate progress the step had set."""
        if on:
            self.ring.emblem.set_indeterminate(True)
        else:
            self.ring.emblem.set_indeterminate(False)
            self.ring.emblem.set_fraction(self._fraction)

    def set_extra(self, widget=None):
        while child := self.extra.get_first_child():
            self.extra.remove(child)
        if widget is not None:
            self.extra.append(widget)

    def set_actions(self, primary=None, secondary=None):
        for btn, spec in ((self.primary_btn, primary),
                          (self.secondary_btn, secondary)):
            if getattr(btn, "_handler", None):
                btn.disconnect(btn._handler)
                btn._handler = None
            if spec is None:
                btn.set_visible(False)
                continue
            label, cb = spec
            btn.set_label(label)
            btn.set_visible(True)
            btn._handler = btn.connect("clicked", lambda _b, f=cb: f())
        self.back_btn.set_visible(self.index > 0)

    # --- navigation -------------------------------------------------------
    def advance(self):
        if self.index + 1 < len(self.steps):
            self._show(self.index + 1)

    def back(self):
        if self.index > 0:
            self._show(self.index - 1, forward=False)

    def mark_complete(self):
        self.completed = True

    def _slide(self, margin):
        """Shift the page sideways without changing its width: the two
        margins always add up to 2 * SHIFT, so nothing reflows mid-slide."""
        m = round(min(max(margin, 0), 2 * SHIFT))
        self.page.set_margin_start(m)
        self.page.set_margin_end(2 * SHIFT - m)

    def _show(self, index, forward=True, animate=True):
        step = self.steps[index]
        self.index = index
        self.reached = max(self.reached, index)
        self.ring.show_step(index, index, step.icon, animate=animate)
        self.set_actions()
        self.set_extra(None)
        self.set_status("")
        self.set_progress(None)

        def swap():
            self.title.set_label(step.title)
            step.enter()

        if not animate:
            swap()
            return
        # The page leaves the way we are travelling and the next one
        # arrives from behind it, so forward and back feel opposite.
        # Margins cannot go negative, so SHIFT is the resting position.
        d = SHIFT if forward else -SHIFT
        t0 = [None]
        phase = ["out"]

        def tick(_w, clock):
            now = clock.get_frame_time() / 1e6
            if t0[0] is None:
                t0[0] = now
            p = min((now - t0[0]) / (SLIDE_S / 2), 1.0)
            if phase[0] == "out":
                self.page.set_opacity(1 - p)
                self._slide(SHIFT - d * p)
                if p >= 1.0:
                    swap()
                    phase[0] = "in"
                    t0[0] = now
                return True
            self.page.set_opacity(p)
            self._slide(SHIFT + d * (1 - p))
            return p < 1.0

        self.page.add_tick_callback(tick)

    def _on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            if self.index > 0:
                self.back()
            else:
                self.close()
            return True
        return False

    def _on_close(self, *_):
        # Record that the wizard was seen either way, so the tray stops
        # offering first-run setup; `dictate setup` re-runs it.
        from dictatr.settings import setup_seen
        if not self.completed and not setup_seen():
            write_config({"setup_done": False})
        return False


class SetupApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.rebreda.dictatr.setup")
        self.win = None

    def do_activate(self):
        if self.win is None:
            self.win = Wizard(self)
            self.win.connect("close-request", self._closed)
        self.win.present()

    def _closed(self, *_):
        self.win = None
        return False


def main():
    sys.exit(SetupApp().run([a for a in sys.argv if not a.startswith("--")]))


if __name__ == "__main__":
    main()
