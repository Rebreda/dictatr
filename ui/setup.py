#!/usr/bin/env python3
"""First-run setup wizard for dictatr, as a radial surface.

Three steps, each a probe plus a small ring of choices: the inference
engine, the hotkeys, and a real dictation that also asks for the typing
permission it needs. Short, skippable, re-runnable (`dictate setup`), so
the packages never have to print shell instructions after install.

It is the same kind of object as the menu and the voice chat, not a
dialog with a ring drawn on it: a transparent layer-shell overlay whose
input region is clipped to what is visible, so the desktop underneath
stays clickable. The step's actions ARE the satellites; the hub is the
step emblem and wears the progress arc while something long runs, and
walking between steps is the kit's own twirl (Ring.swap). The hub goes
back a step, and closes the wizard on the first, exactly as the menu's
hub does.

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
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

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
from radial import BLUE, CHECK_ICON, GREEN, INK, Bubble, Ring  # noqa: E402

APP_ID = "io.github.rebreda.dictatr"
PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
GS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
TRAY_BUS = "io.github.rebreda.dictatr.tray"
CARD_W = 460

# Same four actions the tray binds; see PORTAL_SHORTCUTS in ui/tray.py.
SHORTCUTS = [
    ("dictate", "Dictate at cursor", "CTRL+ALT+d"),
    ("menu", "Open the dictate menu", "CTRL+ALT+space"),
    ("cancel", "Cancel dictation", "CTRL+ALT+c"),
    ("listen", "Toggle always-on capture", "CTRL+ALT+a"),
]

SETUP_CSS = f"""
.card {{
  background: alpha(#1c1d22, 0.94);
  border: 1px solid alpha(#ffffff, 0.10);
  border-radius: 18px;
  padding: 18px 22px;
}}
.title {{ font-size: 17px; font-weight: 700; color: {INK}; }}
.body {{ color: alpha({INK}, 0.72); }}
.pill {{
  background: alpha(#1c1d22, 0.94);
  border: 1px solid alpha(#ffffff, 0.10);
  border-radius: 9999px;
  padding: 6px 14px;
  font-size: 13px;
  color: alpha({INK}, 0.72);
}}
.pill.good {{ color: {GREEN}; border-color: alpha({GREEN}, 0.45); }}
.pill.bad {{ color: #f28b82; border-color: alpha(#f28b82, 0.45); }}
.setup entry {{
  background: alpha(#ffffff, 0.06); color: {INK};
  border: 1px solid alpha(#ffffff, 0.12); border-radius: 8px;
  padding: 7px 10px;
}}
.setup entry:focus-within {{ border-color: alpha({BLUE}, 0.7); }}
""".encode()


def layer_shell():
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LS
        return LS if LS.is_supported() else None
    except (ValueError, ImportError):
        return None


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
        for sid, desc, trigger in SHORTCUTS:
            row = Gtk.Box(spacing=10)
            left = Gtk.Label(label=desc, xalign=0, hexpand=True)
            left.add_css_class("body")
            right = Gtk.Label(label=trigger.replace("CTRL", "Ctrl")
                              .replace("ALT", "Alt").replace("+", " + "),
                              xalign=1)
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
        w.set_status("Listening. Say something, then pause.")
        w.set_items([])
        _in_background(self._worker)

    def _worker(self):
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

def portal_bus() -> Gio.DBusConnection:
    """A private session-bus connection for portal work.

    The portal ties an app id to the connection that first speaks to it,
    and a connection can only be registered once. GTK has already used
    the shared session bus by the time the wizard runs (the Settings
    portal, for the colour scheme), so the shared connection arrives
    associated with an empty id, Register then fails with "already
    associated", and GlobalShortcuts refuses the session with "An app id
    is required". On a connection of our own, Register goes first.
    """
    addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    return Gio.DBusConnection.new_for_address_sync(
        addr,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)


class Binder:
    """One GlobalShortcuts bind dance: CreateSession, BindShortcuts, read
    back the triggers the desktop actually assigned, then close the
    session (the tray hosts the live one). Async so the dialog does not
    freeze the window."""

    def __init__(self, done):
        self.done = done
        self.bus = portal_bus()
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
    """The surface: a card of prose above a ring of choices, floating on
    a transparent overlay whose input region is clipped to both, so the
    desktop underneath keeps working while setup is open."""

    def __init__(self, app):
        super().__init__(application=app, decorated=False,
                         title="Set up dictatr")
        radial.apply_css(SETUP_CSS)
        self.add_css_class("setup")

        self.steps = [EngineStep(self), HotkeysStep(self), SpeakStep(self)]
        self.index = 0
        self.completed = False
        self._fraction = 0.0
        self._closing = False

        self.title_label = Gtk.Label(xalign=0.5, wrap=True,
                                     justify=Gtk.Justification.CENTER)
        self.title_label.add_css_class("title")
        self.body = Gtk.Label(xalign=0.5, wrap=True,
                              justify=Gtk.Justification.CENTER)
        self.body.add_css_class("body")
        self.body.set_max_width_chars(46)
        self.extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("card")
        card.set_size_request(CARD_W, -1)
        for w in (self.title_label, self.body, self.extra):
            card.append(w)

        self.status = Gtk.Label(label="")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.set_max_width_chars(56)
        self.status.add_css_class("pill")
        status_row = Gtk.Box(halign=Gtk.Align.CENTER)
        status_row.append(self.status)

        # The ring starts empty: a step fills it once its probe answers.
        self.ring = Ring([], hub_icon=self.steps[0].icon,
                         hub_tooltip="Close", on_root_hub=self.on_hub)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        column.append(card)
        column.append(status_row)
        column.append(self.ring)
        self.column = column
        self._hit = (card, status_row, self.ring)

        ls = layer_shell()
        self.overlay = ls is not None
        if self.overlay:
            ls.init_for_window(self)
            ls.set_layer(self, ls.Layer.OVERLAY)
            ls.set_keyboard_mode(self, ls.KeyboardMode.ON_DEMAND)
            for edge in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT,
                         ls.Edge.RIGHT):
                ls.set_anchor(self, edge, True)
            GLib.timeout_add(250, self._update_input_region)
        self.set_child(column)

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
        step = self.steps[start]
        self.ring.hub.set_icon_name(step.icon)
        self.title_label.set_label(step.title)
        self.connect("map", lambda *_: GLib.idle_add(self._opened))

    def _opened(self):
        self.ring.open()
        self.steps[self.index].enter()
        return False

    def _update_input_region(self):
        """Clip input to the card, the status pill and the ring: clicks
        anywhere else fall through, so the overlay never blocks the
        desktop the way a modal dialog would."""
        if self._closing:
            return False
        surface = self.get_surface()
        if surface is None:
            return True
        region = cairo.Region()
        for widget in self._hit:
            ok, b = widget.compute_bounds(self)
            if ok and b.size.width > 0:
                region.union(cairo.RectangleInt(
                    int(b.origin.x) - 4, int(b.origin.y) - 4,
                    int(b.size.width) + 8, int(b.size.height) + 8))
        surface.set_input_region(region)
        return True

    # --- what the steps call ---------------------------------------------
    def set_body(self, text):
        self.body.set_label(text)

    def set_status(self, text, tone=""):
        for t in ("good", "bad"):
            self.status.remove_css_class(t)
        if tone:
            self.status.add_css_class(tone)
        self.status.set_label(text)
        self.status.set_visible(bool(text))

    def set_progress(self, fraction):
        """A determinate arc on the hub, or None to clear it."""
        self._fraction = 0.0 if fraction is None else fraction
        self.ring.set_fraction(self._fraction)

    def busy(self, on):
        """Spin the hub arc while a worker runs; stopping restores
        whatever determinate progress the step had set."""
        self.ring.set_indeterminate(bool(on))
        if not on:
            self.ring.set_fraction(self._fraction)

    def set_extra(self, widget=None):
        while child := self.extra.get_first_child():
            self.extra.remove(child)
        if widget is not None:
            self.extra.append(widget)

    def set_items(self, bubbles):
        """Replace the ring's satellites with this step's choices."""
        self.ring.swap(list(bubbles))

    def mark_complete(self):
        self.completed = True

    # --- navigation --------------------------------------------------------
    def advance(self):
        if self.index + 1 < len(self.steps):
            self._show(self.index + 1)

    def back(self):
        if self.index > 0:
            self._show(self.index - 1, forward=False)

    def on_hub(self):
        """The hub is Back, and Close on the first step: the same
        contract the menu's hub has."""
        if self.index > 0:
            self.back()
        else:
            self.close()

    def _show(self, index, forward=True):
        self.index = index
        step = self.steps[index]
        self.set_extra(None)
        self.set_status("")
        self.set_progress(None)
        self.title_label.set_label(step.title)
        self.ring.hub.set_icon_name(step.icon)
        self.ring.hub.set_tooltip_text(
            "Back" if index else "Close")
        self.ring.swap([], forward=forward, done=step.enter)

    def _on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.on_hub()
            return True
        return self.ring.handle_key(keyval)

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
