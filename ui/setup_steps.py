#!/usr/bin/python3
"""What the wizard says, and what each answer does.

Three steps -- the inference engine, the hotkeys, one real dictation --
each a probe on a worker thread and a few labelled choices. This is the
content; ui/setup.py is the window that renders it, and the seam between
them is nine methods wide:

    set_body/set_status/set_extra   what is written
    set_items/set_progress/busy     what can be pressed, and waiting
    advance/mark_complete/close     where the conversation goes next

A step never touches a widget of the window's, and the window never
looks inside a step past its title, icon and enter(). That is what makes
the wizard editable: adding a step is writing one class here, and how it
looks is not this file's business.

Nothing blocks the GTK loop: every probe, download and portal dance runs
on a worker thread and reports back through GLib.idle_add.
"""

import shutil
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DICTATE = str(REPO / "bin" / "dictate")
PORTAL_HELPER = str(REPO / "ui" / "portal_typed.py")
# The helper needs PyGObject, and so did we: reuse this interpreter
# rather than whatever "python3" means on the caller's PATH.
PYTHON = sys.executable or "python3"
TRAY_BUS = "io.github.rebreda.dictatr.tray"

sys.path.insert(0, str(REPO / "src"))
from dictatr import dbus as busclient  # noqa: E402
from dictatr.dbus import name_has_owner as dbus_name_has_owner  # noqa: E402
from dictatr.settings import settings, write_config  # noqa: E402

sys.path.insert(0, str(REPO / "ui"))
import portal_typed  # noqa: E402  (pure helpers only; its gi imports are lazy)
import portal  # noqa: E402
from shortcuts import SHORTCUTS, pretty  # noqa: E402
from radial import CHECK_ICON, Bubble  # noqa: E402

GS_IFACE = portal.GLOBAL_SHORTCUTS
Binder = portal.ShortcutBinder


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
        version = portal.version(GS_IFACE)
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
        Binder(self._bound, SHORTCUTS).run()

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
        if not dbus_name_has_owner(TRAY_BUS):
            try:
                subprocess.Popen([str(REPO / "bin" / "dictate-tray")],
                                 start_new_session=True,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                pass
            return
        bus = busclient.session()
        if bus is None:
            return
        with bus:
            try:
                bus.call(TRAY_BUS, "/Shortcuts",
                         "io.github.rebreda.dictatr.Shortcuts", "Rebind",
                         timeout=5)
            except busclient.DBusError as e:
                print(f"dictatr setup: tray rebind failed: {e}",
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


# The order of the conversation, which is content and not chrome: the
# engine has to exist before a hotkey is worth binding, and both before
# there is anything to try.
STEPS = (EngineStep, HotkeysStep, SpeakStep)
