#!/usr/bin/env python3
"""First-run setup wizard for dictatr, shaped like the voice chat.

Three steps, each a probe plus a few choices: the inference engine, the
hotkeys, and a real dictation that also asks for the typing permission
it needs. Short, skippable, re-runnable (`dictate setup`), so the
packages never have to print shell instructions after install.

It is a conversation, in the same visual family as the chat: pills grow
upward from a hub row on a transparent layer-shell overlay, the wizard's
lines on the left, the choice you made on the right. Choices are
labelled pills, because the ring this replaced put every action behind
an unlabelled icon and a hover, spent its single line of prose on an
ellipsis, and had nowhere to print a number on a download that runs for
minutes. The input region is clipped to the column, so the desktop
underneath stays clickable.

Nothing blocks the GTK loop: every probe, download and portal dance runs
on a worker thread and reports back through GLib.idle_add.

DICTATR_SETUP_STEP=N opens straight on one step, for capture and for
eyeballing a step without walking the whole wizard.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import cairo
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
import portal  # noqa: E402
import radial  # noqa: E402
from shortcuts import SHORTCUTS, pretty  # noqa: E402
from radial import BLUE, CHECK_ICON, GREEN, INK, Bubble  # noqa: E402

APP_ID = portal.APP_ID
PORTAL_BUS = portal.BUS
PORTAL_PATH = portal.PATH
GS_IFACE = portal.GLOBAL_SHORTCUTS
TRAY_BUS = "io.github.rebreda.dictatr.tray"
WIDTH = 420
STACK_H = 640   # pills + choices + status + hub; the hub sits at the bottom

# The same visual vocabulary as the voice chat, because this is the same
# kind of conversation: the wizard says something, you answer by picking
# one of a few readable pills, and what you picked stays in the
# transcript above. No card, no ring of unlabelled icons — a choice
# whose meaning only appears on hover is a choice nobody reads.
SETUP_CSS = f"""
window {{ background: transparent; }}
.step-pill {{
  background: alpha(#1c1d22, 0.85);
  border: 1px solid alpha(#ffffff, 0.08);
  border-radius: 9999px; padding: 2px 11px;
  color: alpha({INK}, 0.5); font-size: 11px;
}}
/* A choice is a labelled pill, not an icon on an orbit. The icon is
   decoration; the words are the affordance. */
.choice {{
  border-radius: 9999px;
  padding: 8px 16px;
  background: alpha(#1c1d22, 0.93);
  border: 1px solid alpha(#ffffff, 0.12);
  color: {INK};
  transition: border-color 120ms ease, background 120ms ease;
}}
.choice:hover {{
  border-color: alpha({BLUE}, 0.65);
  background: alpha({BLUE}, 0.14);
}}
.choice image {{ color: alpha({INK}, 0.7); }}
.choice.primary {{ border-color: alpha({BLUE}, 0.5); }}
/* The number that picks this from the keyboard: present enough to be
   learned, quiet enough not to compete with the words. */
.choice-key {{
  color: alpha({INK}, 0.30); font-size: 11px;
  padding-left: 6px;
}}
.choice.primary image {{ color: {BLUE}; }}
.setup progressbar trough {{
  min-height: 5px; border-radius: 9999px;
  background: alpha(#ffffff, 0.10);
}}
.setup progressbar progress {{
  min-height: 5px; border-radius: 9999px;
  background: {BLUE};
}}
.setup entry {{
  background: alpha(#ffffff, 0.06); color: {INK};
  border: 1px solid alpha(#ffffff, 0.12); border-radius: 8px;
  padding: 7px 10px;
}}
.setup entry:focus-within {{ border-color: alpha({BLUE}, 0.7); }}
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


def _human(n):
    """Bytes as a size a person can judge a download by."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


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


class Step:
    """One step. enter() probes on a worker thread and calls the
    surface's setters; the surface owns every widget."""

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
        except Exception as e:            # never let a probe kill the step
            GLib.idle_add(self._probe_failed, str(e))
            return
        GLib.idle_add(self._probed, found, lemond.status())

    def _probe_failed(self, msg):
        w = self.wiz
        w.busy(False)
        w.set_status(f"Could not probe: {msg}", "bad")
        w.set_items([Bubble("folder-download-symbolic",
                            "Set up the built-in engine", self._install)])

    def _probed(self, found, managed):
        w = self.wiz
        w.busy(False)
        if found:
            kind, base = found
            names = {"managed": "The built-in engine is running",
                     "system": "Found a Lemonade server",
                     "custom": "Using your configured endpoint"}
            w.set_status(f"{names[kind]} at {base}", "good")
            w.set_items([
                Bubble("go-next-symbolic", "Continue",
                       lambda: self._keep(kind, base)),
                Bubble("network-server-symbolic", "Use something else",
                       self._show_custom),
            ])
            return
        have = "installed" if managed["binary"] else "a 7 MB download"
        w.set_status(f"No server running. The built-in engine is {have}; "
                     "the speech model is about 1 GB, downloaded once.")
        w.set_items([
            Bubble("folder-download-symbolic", "Set up the built-in engine",
                   self._install),
            Bubble("network-server-symbolic", "Use a custom endpoint",
                   self._show_custom),
        ])

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
        w.set_items([])
        w.set_status("Preparing the built-in engine…")
        _in_background(self._install_worker)

    def _install_worker(self):
        from dictatr.backend import lemond
        try:
            if not (lemond.VENDORED.is_file() or lemond.DOWNLOADED.is_file()):
                GLib.idle_add(self.wiz.set_status, "Downloading the engine…")
                lemond.download_lemond(
                    lambda pct: GLib.idle_add(
                        self.wiz.set_progress, pct / 100,
                        f"Downloading the engine — {pct:.0f}%"))
            GLib.idle_add(self.wiz.set_progress, None)
            GLib.idle_add(self.wiz.set_status, "Starting the engine…")
            lemond.start()
            write_config({"backend": "managed"})
            model = settings.whisper.model
            GLib.idle_add(self.wiz.set_status, f"Downloading {model}…")

            def on_event(ev):
                # Say which file, how far, and how much: the bare arc
                # this replaced made a stalled gigabyte and a slow one
                # look exactly the same.
                pct = ev.get("percent")
                got = ev.get("bytes_downloaded")
                parts = [ev.get("file") or model]
                if got:
                    total = got * 100 / pct if pct else 0
                    parts.append(f"{_human(got)} of {_human(total)}"
                                 if total else _human(got))
                if pct is not None:
                    parts.append(f"{float(pct):.0f}%")
                GLib.idle_add(self.wiz.set_progress,
                              float(pct) / 100 if pct is not None else 0.0,
                              " — ".join(parts))

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
        w.set_items([
            Bubble("view-refresh-symbolic", "Try again", self._install),
            Bubble("network-server-symbolic", "Use a custom endpoint",
                   self._show_custom),
        ])

    def _installed(self):
        w = self.wiz
        w.busy(False)
        w.set_progress(1.0)
        w.set_status("The built-in engine is ready", "good")
        w.set_items([Bubble("go-next-symbolic", "Continue", w.advance)])

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
        w.set_items([
            Bubble("go-next-symbolic", "Test and save",
                   lambda: self._test_custom(url, key)),
            Bubble("go-previous-symbolic", "Back to the built-in engine",
                   self.enter),
        ])

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
        w.set_extra(None)
        w.set_status(f"Connected: {n} model(s) available", "good")
        w.set_items([Bubble("go-next-symbolic", "Continue", w.advance)])


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
        skip = Bubble("go-next-symbolic", "Skip", w.advance)
        if version is not None:
            w.set_status("Your desktop can bind these. One dialog, then "
                         "they stay bound.")
            w.set_items([Bubble("dictatr-hotkey-symbolic",
                                "Bind the shortcuts", self._bind), skip])
        elif kde:
            w.set_status("No shortcuts portal here. dictatr can write them "
                         "into the KDE config instead (active after you log "
                         "back in).")
            w.set_items([Bubble("document-edit-symbolic",
                                "Write KDE shortcuts", self._legacy), skip])
        else:
            w.set_status("This desktop has no shortcuts portal. Bind the "
                         "commands below by hand in its settings.", "bad")
            w.set_items([Bubble("go-next-symbolic", "Continue", w.advance)])

    def _table(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.rows = {}
        for sid, desc, trigger, _cmd in SHORTCUTS:
            row = Gtk.Box(spacing=10)
            left = Gtk.Label(label=desc, xalign=0, hexpand=True)
            left.add_css_class("body")
            right = Gtk.Label(label=pretty(trigger), xalign=1)
            right.add_css_class("body")
            row.append(left)
            row.append(right)
            box.append(row)
            self.rows[sid] = right
        return box

    def _bind(self):
        w = self.wiz
        w.busy(True)
        w.set_status("Waiting for your desktop's shortcut dialog…")
        w.set_items([])
        Binder(self._bound).run()

    def _bound(self, ok, results, msg):
        w = self.wiz
        w.busy(False)
        if not ok:
            w.set_status(msg or "The request was refused", "bad")
            w.set_items([
                Bubble("view-refresh-symbolic", "Try again", self._bind),
                Bubble("go-next-symbolic", "Skip", w.advance),
            ])
            return
        for sid, meta in results:
            if sid in self.rows:
                self.rows[sid].set_label(
                    meta.get("trigger_description") or "bound")
        self._ensure_tray()
        w.set_status("Shortcuts bound", "good")
        w.set_items([Bubble("go-next-symbolic", "Continue", w.advance)])

    def _ensure_tray(self):
        """Hand the live shortcut session back to the tray.

        Binding above happened in this window's own portal session, and
        that session dies with the window: whichever session bound last
        owns the keys, so leaving it there is what made the shortcuts go
        quiet after setup. A running tray is told to rebind; a missing
        one is started, and binds on the way up."""
        if not _name_has_owner(TRAY_BUS):
            try:
                subprocess.Popen([str(REPO / "bin" / "dictate-tray")],
                                 start_new_session=True,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                pass
            return
        try:
            Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
                TRAY_BUS, "/Shortcuts",
                "io.github.rebreda.dictatr.Shortcuts", "Rebind",
                None, None, Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as e:
            print(f"dictatr setup: tray rebind failed: {e.message}",
                  file=sys.stderr)

    def _legacy(self):
        self.wiz.busy(True)
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
        w.set_items([Bubble("go-next-symbolic", "Continue", w.advance)])


class SpeakStep(Step):
    """Permission and proof in one step: the thing that verifies typing
    is the user's own voice, not a canned string typed into a box."""

    key, icon = "speak", "dictatr-mic-symbolic"
    title = "Try it"

    def enter(self):
        w = self.wiz
        w.set_body("Dictation types wherever your cursor is, which needs "
                   "one permission from your desktop. Then say a sentence "
                   "and watch it land.")
        w.set_status("Checking…")
        w.busy(True)
        self.entry = Gtk.Entry(placeholder_text="your words land here",
                               hexpand=True)
        w.set_extra(self.entry)
        _in_background(self._probe)

    def _probe(self):
        # --check exits 0 only when the portal can inject keyboard events.
        portal = _run([PYTHON, PORTAL_HELPER, "--check"]).returncode == 0
        granted = bool(portal_typed.load_token())
        GLib.idle_add(self._probed, portal, granted)

    def _probed(self, portal, granted):
        w = self.wiz
        w.busy(False)
        if granted and settings.typing.portal:
            self._ready("Typing is allowed on this desktop", "good")
        elif not settings.typing.portal:
            self._ready("Typing at the cursor is switched off, so "
                        "transcripts go to the clipboard.", "bad")
        elif portal:
            w.set_status("Your desktop can grant this. The dialog appears "
                         "once and the permission is remembered.")
            w.set_items([
                Bubble("dictatr-typing-symbolic", "Allow typing", self._grant),
                Bubble("edit-copy-symbolic", "Skip, use the clipboard",
                       self._ready),
            ])
        else:
            self._ready("Nothing here can type at the cursor, so transcripts "
                        "go to the clipboard. Dictation still works.", "bad")

    def _grant(self):
        w = self.wiz
        w.busy(True)
        w.set_status("Waiting for your desktop's permission dialog…")
        w.set_items([])
        _in_background(self._grant_worker)

    def _grant_worker(self):
        r = _run([PYTHON, PORTAL_HELPER, "--grant"], timeout=150)
        GLib.idle_add(self._granted, r.returncode == 0,
                      (r.stderr or r.stdout or "").strip())

    def _granted(self, ok, msg):
        w = self.wiz
        w.busy(False)
        if ok:
            self._ready("Permission granted", "good")
        else:
            w.set_status(msg or "The request was refused", "bad")
            w.set_items([
                Bubble("view-refresh-symbolic", "Try again", self._grant),
                Bubble("edit-copy-symbolic", "Skip, use the clipboard",
                       self._ready),
            ])

    # --- the dictation ---------------------------------------------------
    def _ready(self, status="Ready when you are", tone=""):
        w = self.wiz
        w.set_status(status, tone)
        w.set_items([
            Bubble("dictatr-mic-symbolic", "Start dictation", self._go),
            Bubble(CHECK_ICON, "Finish", self._finish),
        ])

    def _go(self):
        w = self.wiz
        self.entry.set_text("")
        self.entry.grab_focus()
        w.busy(True)
        w.set_status("Getting ready…")
        w.set_items([])
        _in_background(self._worker)

    def _worker(self):
        # On a fresh install the model is always cold, and loading it
        # happens before anything can listen. Announcing "listening"
        # first is how the first sentence of a new setup gets lost.
        from dictatr.engine import ensure_asr_loaded
        ensure_asr_loaded(lambda m: GLib.idle_add(
            self.wiz.set_status, f"Loading {m}. First time only…"))
        GLib.idle_add(self.wiz.set_status,
                      "Listening. Say something, then pause.")
        r = _run([DICTATE, "type"], timeout=120)
        tail = (r.stderr or "").strip().splitlines()
        GLib.idle_add(self._done, r.returncode == 0, tail[-1] if tail else "")

    def _done(self, ok, tail):
        w = self.wiz
        w.busy(False)
        text = self.entry.get_text().strip()
        again = Bubble("view-refresh-symbolic", "Again", self._go)
        finish = Bubble(CHECK_ICON, "Finish", self._finish)
        if text:
            w.set_status(f"Heard: {text}", "good")
            w.set_items([finish, again])
        elif ok:
            w.set_status("Transcribed, but the text did not land here. It is "
                         "on the clipboard: press Ctrl+V in the box.", "bad")
            w.set_items([finish, again])
        else:
            w.set_status(tail or "Dictation failed. Check the engine step.",
                         "bad")
            w.set_items([again, finish])

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
        self.bus = portal.session_bus()
        self.req = portal.Requests(self.bus, GS_IFACE)
        self.session = None

    def run(self):
        portal.register(self.bus)
        self._request("CreateSession", "(a{sv})", (),
                      {"session_handle_token": GLib.Variant("s", "dictatrsetup")},
                      self._on_session)

    def _request(self, method, sig, args, options, cb, timeout_s=180):
        err = self.req.call(method, sig, args, options, cb, timeout_s)
        if err is not None:
            self.done(False, [], err)

    def _on_session(self, code, results):
        self.session = results.get("session_handle")
        if code or not self.session:
            self.done(False, [], f"the session was refused ({code})")
            return
        shorts = [(sid, {"description": GLib.Variant("s", desc),
                         "preferred_trigger": GLib.Variant("s", trig)})
                  for sid, desc, trig, _cmd in SHORTCUTS]
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
    """The surface: a conversation, in the same shape as the voice chat.

    Pills grow upward from a hub row — what the wizard said, what you
    picked — above the step's choices, a status line and a progress bar.
    It replaced a card with a ring of unlabelled satellites: the ring
    made every choice a hover away from being readable, spent its one
    text line on an ellipsis, and had no room for a number on a download
    that takes minutes. The overlay's input region is still clipped to
    what is visible, so the desktop underneath stays clickable.
    """

    def __init__(self, app):
        super().__init__(application=app, decorated=False,
                         title="Set up dictatr", default_width=WIDTH)
        radial.apply_css(SETUP_CSS)
        self.add_css_class("setup")

        self.steps = [EngineStep(self), HotkeysStep(self), SpeakStep(self)]
        self.index = 0
        self.completed = False
        self._closing = False
        self._pills = []
        self._body = None      # this step's opening pill, so it can be edited

        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        stack.set_size_request(WIDTH, STACK_H)
        stack.append(Gtk.Box(vexpand=True))   # pushes the column down

        # Where you are in the wizard. The ring that used to stand here
        # wore this on its hub as an arc; dropping the ring dropped the
        # only thing that said how much of setup was left.
        self.step_label = Gtk.Label(label="")
        self.step_label.add_css_class("step-pill")
        step_row = Gtk.Box(halign=Gtk.Align.CENTER)
        step_row.append(self.step_label)
        stack.append(step_row)

        self.msgs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, max_content_height=380,
            valign=Gtk.Align.END)
        self.scroll.set_child(self.msgs)
        stack.append(self.scroll)

        # Order matters here: what is true now, then what you can do
        # about it. The status carries the reason the choices exist —
        # how big the download is, what was refused — so it reads
        # before them, not after.
        # Wrapping, not ellipsized: the line that says how big the
        # download is has to survive being read.
        self.status = Gtk.Label(label="", wrap=True,
                                justify=Gtk.Justification.CENTER)
        self.status.set_max_width_chars(44)
        self.status.add_css_class("status-pill")
        status_row = Gtk.Box(halign=Gtk.Align.CENTER)
        status_row.append(self.status)
        stack.append(status_row)

        self.extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             halign=Gtk.Align.CENTER)
        stack.append(self.extra)

        self.choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=7, halign=Gtk.Align.CENTER)
        stack.append(self.choices)

        self.bar = Gtk.ProgressBar(halign=Gtk.Align.CENTER, visible=False)
        self.bar.set_size_request(WIDTH - 140, -1)
        bar_row = Gtk.Box(halign=Gtk.Align.CENTER)
        bar_row.append(self.bar)
        stack.append(bar_row)

        hub_row = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic",
                                   valign=Gtk.Align.CENTER,
                                   tooltip_text="Back")
        self.back_btn.add_css_class("satbtn")
        self.back_btn.add_css_class("back")
        self.back_btn.set_focusable(False)
        self.back_btn.connect("clicked", lambda *_: self.back())
        self.hub = Gtk.Button(icon_name=self.steps[0].icon)
        self.hub.add_css_class("hubbtn")
        self.hub.set_focusable(False)
        self.hub.set_sensitive(False)     # an emblem, not a control
        close = Gtk.Button(icon_name="window-close-symbolic",
                           valign=Gtk.Align.CENTER, tooltip_text="Close  [Esc]")
        close.add_css_class("satbtn")
        close.set_focusable(False)
        close.connect("clicked", lambda *_: self.close())
        hub_row.append(self.back_btn)
        hub_row.append(self.hub)
        hub_row.append(close)
        stack.append(hub_row)

        self.column = stack
        self._hit = (step_row, self.scroll, self.extra, self.choices,
                     status_row, bar_row, hub_row)

        ls = radial.layer_shell()
        self.overlay = ls is not None
        if self.overlay:
            ls.init_for_window(self)
            ls.set_layer(self, ls.Layer.OVERLAY)
            ls.set_keyboard_mode(self, ls.KeyboardMode.ON_DEMAND)
            for edge in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT,
                         ls.Edge.RIGHT):
                ls.set_anchor(self, edge, True)
            GLib.timeout_add(250, self._update_input_region)
        outer = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        outer.append(stack)
        self.set_child(outer)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)

        try:
            start = max(0, min(int(os.environ.get("DICTATR_SETUP_STEP", 0)),
                               len(self.steps) - 1))
        except ValueError:
            start = 0
        self.index = start
        self.connect("map", lambda *_: GLib.idle_add(self._opened))

    def _opened(self):
        self._enter(self.index)
        return False

    def _update_input_region(self):
        if self._closing:
            return False
        return radial.clip_input_region(self, self._hit)

    def say(self, text, role="ai", title=None):
        """Add a pill. The wizard's own lines carry the step title; the
        line you chose comes back as your side of the exchange."""
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        inner.add_css_class("msg")
        inner.add_css_class(f"msg-{role}")
        if title:
            head = Gtk.Label(label=title, xalign=0.0)
            head.add_css_class("title")
            inner.append(head)
        # Not selectable: with no entry on most steps, a selectable
        # label takes the initial focus and opens with its own text
        # highlighted, which reads as a mistake.
        lab = Gtk.Label(label=text, wrap=True, xalign=0.0)
        lab.set_max_width_chars(40)
        lab.add_css_class("body")
        inner.append(lab)

        wrap = Gtk.Box(halign=Gtk.Align.END if role == "user"
                       else Gtk.Align.START)
        wrap.append(inner)
        rev = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=200, child=wrap)
        self.msgs.append(rev)
        self._pills.append(rev)
        GLib.idle_add(rev.set_reveal_child, True)
        self._refade()
        GLib.idle_add(self._scroll_down)
        lab._rev = rev
        return lab

    def _refade(self):
        """Older pills dim stepwise, so the live exchange reads first."""
        n = len(self._pills)
        for i, rev in enumerate(self._pills):
            age = n - 1 - i
            rev.set_opacity(1.0 if age < 2 else max(0.35, 1.0 - 0.16 * age))

    def _scroll_down(self):
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _trim_to(self, count):
        while len(self._pills) > count:
            self.msgs.remove(self._pills.pop())
        self._refade()

    # --- what the steps call ---------------------------------------------
    def set_body(self, text):
        """The step's opening line, as the wizard's own pill."""
        step = self.steps[self.index]
        self._body = self.say(text, "ai", title=step.title)

    def set_status(self, text, tone=""):
        for t in ("good", "bad"):
            self.status.remove_css_class(t)
        if tone:
            self.status.add_css_class(tone)
        self.status.set_label(text)
        self.status.set_visible(bool(text))

    def set_progress(self, fraction, text=None):
        """A real bar with a real number under it. None clears both."""
        if fraction is None:
            self.bar.set_visible(False)
            self.bar.set_fraction(0.0)
            return
        self.bar.set_visible(True)
        self.bar.set_fraction(max(0.0, min(1.0, float(fraction))))
        if text:
            self.set_status(text)

    def busy(self, on):
        """While a worker runs with no percentage to show, pulse."""
        if on:
            self.bar.set_visible(True)
            if self._pulse is None:
                self._pulse = GLib.timeout_add(90, self._do_pulse)
        elif self._pulse is not None:
            GLib.source_remove(self._pulse)
            self._pulse = None
            self.bar.set_visible(self.bar.get_fraction() > 0)

    _pulse = None

    def _do_pulse(self):
        self.bar.pulse()
        return True

    def set_extra(self, widget=None):
        while child := self.extra.get_first_child():
            self.extra.remove(child)
        if widget is not None:
            self.extra.append(widget)

    def set_items(self, bubbles):
        """Render the step's choices as labelled pills.

        Steps hand over radial Bubbles, whose tooltip was always the
        readable name of the action — here that name is the button, and
        the icon is only decoration beside it.
        """
        while child := self.choices.get_first_child():
            self.choices.remove(child)
        for i, b in enumerate(bubbles):
            btn = Gtk.Button()
            btn.add_css_class("choice")
            if i == 0:
                btn.add_css_class("primary")
            row = Gtk.Box(spacing=9)
            row.append(Gtk.Image(icon_name=b.icon))
            row.append(Gtk.Label(label=b.tooltip, hexpand=True, xalign=0.0))
            key = Gtk.Label(label=str(i + 1))
            key.add_css_class("choice-key")
            row.append(key)
            btn.set_child(row)
            # A focused button fires on space or Return, so a stray
            # keypress used to activate whatever held focus.
            btn.set_focusable(False)
            btn.connect("clicked", self._chose, b)
            self.choices.append(btn)

    def _chose(self, _btn, bubble):
        """Picking is a turn: it lands in the transcript, then runs."""
        self.say(bubble.tooltip, "user")
        self.set_items([])
        if bubble.action is not None:
            bubble.action()

    def mark_complete(self):
        self.completed = True

    # --- navigation --------------------------------------------------------
    def advance(self):
        if self.index + 1 < len(self.steps):
            self._enter(self.index + 1)

    def back(self):
        if self.index > 0:
            self._enter(self.index - 1, back=True)
        else:
            self.close()

    def on_hub(self):
        """Esc is Back, and Close on the first step, as it always was."""
        self.back()

    def _enter(self, index, back=False):
        step = self.steps[index]
        if back:
            # Walking back rewinds the transcript to where that step
            # began, so its pills are not said twice.
            self._trim_to(getattr(step, "_mark", 0))
        self.index = index
        step._mark = len(self._pills)
        self.set_extra(None)
        self.set_items([])
        self.set_status("")
        self.set_progress(None)
        self.busy(False)
        self.hub.set_icon_name(step.icon)
        # On the first step there is nothing to go back to, and the row
        # already carries a close button; two controls that both close
        # is one too many.
        self.back_btn.set_visible(index > 0)
        self.step_label.set_label(
            f"Step {index + 1} of {len(self.steps)}")
        step.enter()

    def _choice_buttons(self):
        out, child = [], self.choices.get_first_child()
        while child is not None:
            out.append(child)
            child = child.get_next_sibling()
        return out

    def _on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.on_hub()
            return True
        # Number keys pick, Return takes the first: the same vocabulary
        # radial.Ring.handle_key answers. Nothing in the column is
        # focusable (a focused button fires on space, which used to
        # activate whatever a stray keypress landed on), so without this
        # there is no way through setup that is not a mouse.
        buttons = self._choice_buttons()
        if not buttons:
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            buttons[0].emit("clicked")
            return True
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_0 + len(buttons):
            buttons[keyval - Gdk.KEY_1].emit("clicked")
            return True
        return False

    def _on_close(self, *_):
        # Record that the wizard was seen either way, so the tray stops
        # offering first-run setup; `dictate setup` re-runs it.
        self._closing = True
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
