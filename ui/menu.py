#!/usr/bin/env python3
"""Floating radial menu for dictatr — round action bubbles that twirl out
from the cursor point, in the spirit of Android's floating assistant ball.

GTK4 + PyGObject only (system packages on Fedora/GNOME, fine on KDE, macOS
via Homebrew). Single-instance via GApplication: launching it again while
open closes it, so the hotkey toggles the menu instead of stacking copies.

Cursor positioning without any cursor-query tool: with gtk4-layer-shell the
window is a fullscreen transparent overlay — the compositor reports the
pointer position to it, the bubbles spiral out from exactly there, and a
click anywhere outside dismisses. Without layer-shell (GNOME) the menu is a
small centered window with the same animation.

The ring itself (bubbles, orbits, twirl in/out, the More submenu) lives in
ui/radial.py; this file owns placement, the window, and the actions.

`dictate-menu --settings` opens the settings window directly.
"""

import json
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DICTATE = str(REPO / "bin" / "dictate")
sys.path.insert(0, str(REPO / "src"))
from dictatr import runstate  # noqa: E402
from dictatr.settings import CONFIG_PATH, settings  # noqa: E402

sys.path.insert(0, str(REPO / "ui"))
import radial  # noqa: E402
from radial import SIZE, Bubble, Ring  # noqa: E402

MENU_CSS = b"""
.settings-box { padding: 18px; }
"""


def layer_shell():
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LS
        return LS
    except (ValueError, ImportError):
        return None


class Radial(Gtk.ApplicationWindow):
    def __init__(self, app):
        # NB: resizable must stay True — a non-resizable window rejects the
        # compositor's fullscreen configure, breaking the layer-shell overlay.
        super().__init__(application=app, decorated=False)

        radial.apply_css(MENU_CSS)

        self.circle = self._build_ring()
        self.placed = False
        self._dismissing = False

        ls = layer_shell()
        if ls is not None:
            ls.init_for_window(self)
            ls.set_layer(self, ls.Layer.OVERLAY)
            ls.set_keyboard_mode(self, ls.KeyboardMode.ON_DEMAND)
            for edge in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT,
                         ls.Edge.RIGHT):
                ls.set_anchor(self, edge, True)
            self.canvas = Gtk.Fixed()
            self.set_child(self.canvas)

            motion = Gtk.EventControllerMotion()
            motion.connect("enter", self.on_pointer)
            motion.connect("motion", self.on_pointer)
            self.add_controller(motion)
            # KWin doesn't send pointer-enter until the mouse moves, so ask
            # the surface for the pointer position once we're mapped.
            # wlroots compositors answer only after a pointer event, which
            # can't happen before the first frame is on screen — so the
            # give-up countdown must not start until then.
            self._polls = 0
            self._ticks = 0
            self.add_tick_callback(self._mark_painted)
            GLib.timeout_add(50, self.poll_pointer)

            outside = Gtk.GestureClick()
            outside.connect("pressed", self.on_outside_click)
            self.canvas.add_controller(outside)

            # Drag the hub to move the whole circle; small movements
            # still count as clicks (claim only past a threshold).
            self._pos = (0, 0)
            self._drag_from = None
            drag = Gtk.GestureDrag()
            drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            drag.connect("drag-begin", self.on_drag_begin)
            drag.connect("drag-update", self.on_drag_update)
            drag.connect("drag-end", self.on_drag_end)
            self.canvas.add_controller(drag)
        else:
            self.set_default_size(SIZE, SIZE)
            self.canvas = None
            self.set_child(self.circle)
            self.connect("map", lambda *_: GLib.idle_add(self.circle.open))

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def _build_ring(self):
        # Root ring, top position first, then clockwise.
        listen_css = ("on",) if runstate.live_pid(runstate.LISTEN_PID) else ()
        items = [
            Bubble("audio-input-microphone-symbolic",
                   "Dictate (type at cursor)", self.run(["type"])),
            Bubble("edit-copy-symbolic", "Dictate to clipboard",
                   self.run(["clip"])),
            Bubble("user-available-symbolic", "Ask the AI (voice chat)",
                   self.chat),
            Bubble("media-record-symbolic", "Always-on capture (toggle)",
                   self.run(["listen", "--toggle"]), css=listen_css),
            Bubble("view-more-symbolic", "More", children=[
                Bubble("folder-music-symbolic", "Transcribe audio file…",
                       self.pick_file),
                Bubble("user-trash-symbolic", "Clean up archive",
                       self.gc),
                Bubble("emblem-system-symbolic", "Settings",
                       self.open_settings),
            ]),
            Bubble("process-stop-symbolic", "Cancel recording",
                   self.run(["cancel"])),
        ]
        return Ring(items, hub_icon="audio-input-microphone-symbolic",
                    hub_tooltip="Close", on_root_hub=self.dismiss)

    # --- overlay-mode placement ---------------------------------------
    def place_at(self, x, y):
        if self.placed or self.canvas is None:
            return
        self.placed = True
        w = self.get_width() or SIZE
        h = self.get_height() or SIZE
        x = min(max(x - SIZE / 2, 0), max(w - SIZE, 0))
        y = min(max(y - SIZE / 2, 0), max(h - SIZE, 0))
        self._pos = (x, y)
        self.canvas.put(self.circle, x, y)
        self.circle.open()

    def on_pointer(self, _c, x, y):
        self.place_at(x, y)

    def _mark_painted(self, _w, _clock):
        # The first tick starts the first frame; only the second proves a
        # frame is actually on screen (the first paint of a 4K surface can
        # take a second on a software renderer).
        self._ticks += 1
        return self._ticks < 2

    def poll_pointer(self):
        if self.placed:
            return False
        surface = self.get_surface()
        if surface is not None and self.get_width() > 0:
            seat = Gdk.Display.get_default().get_default_seat()
            ok, x, y, _mask = surface.get_device_position(seat.get_pointer())
            if ok and (x or y):
                self.place_at(x, y)
                return False
            if self._ticks >= 2:
                self._polls += 1
        if self._polls > 40 and self.get_width() > 0:
            # Pointer is on another output (or query unsupported): center.
            self.place_at(self.get_width() / 2, self.get_height() / 2)
            return False
        return True

    def _hub_at(self, x, y):
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None:
            if target is self.circle.hub:
                return True
            target = target.get_parent()
        return False

    def on_drag_begin(self, _g, x, y):
        self._drag_from = self._pos if self._hub_at(x, y) else None

    def on_drag_update(self, g, dx, dy):
        if self._drag_from is None:
            return
        if abs(dx) > 8 or abs(dy) > 8:
            g.set_state(Gtk.EventSequenceState.CLAIMED)
        self.canvas.move(self.circle,
                         self._drag_from[0] + dx, self._drag_from[1] + dy)

    def on_drag_end(self, _g, dx, dy):
        if self._drag_from is not None:
            self._pos = (self._drag_from[0] + dx, self._drag_from[1] + dy)
            self._drag_from = None

    def on_outside_click(self, _g, _n, x, y):
        # A press outside the circle's buttons dismisses the menu.
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None:
            if isinstance(target, Gtk.Button):
                return  # the button handles it
            target = target.get_parent()
        self.dismiss()

    def dismiss(self):
        """Twirl the bubbles back into the hub, then close. A second
        request (hotkey mashing) closes immediately."""
        if self._dismissing or (self.canvas is not None and not self.placed):
            self.close()
            return
        self._dismissing = True
        self.circle.dismiss(then=self.close)

    # --- actions -------------------------------------------------------
    def on_key(self, _c, keyval, _code, _state):
        if self.circle.handle_key(keyval):
            return True
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False

    def run(self, args):
        def action():
            subprocess.Popen([DICTATE, *args])
            self.close()
        return action

    def chat(self):
        subprocess.Popen([str(REPO / "bin" / "dictate-chat")])
        self.close()

    def gc(self):
        # Detached: the shell survives the menu closing, and reports the
        # result with a notification like the tray does.
        subprocess.Popen(
            ["sh", "-c",
             f'out=$("{DICTATE}" gc 2>/dev/null); '
             f'notify-send -a Dictate "Archive gc" "${{out:-done}}"'],
            start_new_session=True)
        self.close()

    def open_settings(self):
        SettingsWindow(self.get_application()).present()
        self.close()

    def pick_file(self):
        dialog = Gtk.FileDialog(title="Transcribe audio file")
        f = Gtk.FileFilter()
        f.set_name("Audio files")
        for m in ("audio/x-wav", "audio/mpeg", "audio/mp4", "audio/ogg",
                  "audio/flac", "audio/webm"):
            f.add_mime_type(m)
        dialog.set_default_filter(f)

        def done(d, res):
            try:
                gfile = d.open_finish(res)
            except GLib.Error:
                self.close()
                return
            subprocess.Popen([DICTATE, "file", gfile.get_path()])
            self.close()

        dialog.open(self, None, done)


class SettingsWindow(Gtk.Window):
    """Edit ~/.config/dictatr/config.toml (env vars still override)."""

    def __init__(self, app):
        super().__init__(application=app, title="Dictate settings",
                         default_width=380, resizable=False)

        grid = Gtk.Grid(row_spacing=12, column_spacing=12)
        grid.add_css_class("settings-box")

        def row(y, label, widget):
            lab = Gtk.Label(label=label, xalign=0.0)
            grid.attach(lab, 0, y, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, y, 1, 1)

        self.model_dd = Gtk.DropDown.new_from_strings([settings.whisper.model])
        row(0, "Dictation model", self.model_dd)

        self.llm_dd = Gtk.DropDown.new_from_strings([settings.llm.model])
        row(1, "Ask model", self.llm_dd)
        threading.Thread(target=self._load_models, daemon=True).start()

        self.silence = Gtk.SpinButton.new_with_range(300, 3000, 100)
        self.silence.set_value(settings.vad.silence_duration_ms)
        row(2, "Segment pause (ms)", self.silence)

        self.idle = Gtk.SpinButton.new_with_range(1.0, 8.0, 0.5)
        self.idle.set_digits(1)
        self.idle.set_value(settings.vad.idle_s)
        row(3, "Finish after quiet (s)", self.idle)

        self.speak_sw = Gtk.Switch(
            halign=Gtk.Align.START, active=settings.llm.speak)
        row(4, "Speak answers aloud", self.speak_sw)

        self.archive_sw = Gtk.Switch(
            halign=Gtk.Align.START, active=settings.storage.enabled)
        row(5, "Archive recordings", self.archive_sw)

        self.archive_dir = Gtk.Entry()
        self.archive_dir.set_text(
            settings.storage.base if settings.storage.enabled
            else str(Path.home() / ".listenr" / "dictation"))
        row(6, "Archive folder", self.archive_dir)

        self.notify_checks = {}
        nbox = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                           max_children_per_line=3, column_spacing=6)
        for key, label in (("state", "State"), ("delivery", "Delivery"),
                           ("answers", "Answers"), ("toggles", "Toggles"),
                           ("errors", "Errors")):
            cb = Gtk.CheckButton(label=label)
            cb.set_active(getattr(settings.notify, key))
            self.notify_checks[key] = cb
            nbox.append(cb)
        row(7, "Notifications", nbox)

        note = Gtk.Label(
            label=f"Saved to {CONFIG_PATH}\nEnvironment variables override.",
            xalign=0.0)
        note.add_css_class("dim-label")
        grid.attach(note, 0, 8, 2, 1)

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.on_save)
        grid.attach(save, 1, 9, 1, 1)

        self.set_child(grid)

    def _load_models(self):
        try:
            url = f"{settings.whisper.api_base}/models"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.load(r)["data"]
            asr = [m["id"] for m in data
                   if "transcription" in (m.get("labels") or [])]
            llms = [m["id"] for m in data
                    if "transcription" not in (m.get("labels") or [])
                    and "kokoro" not in m["id"].lower()]
        except Exception:
            return
        if settings.whisper.model not in asr:
            asr.insert(0, settings.whisper.model)
        if settings.llm.model not in llms:
            llms.insert(0, settings.llm.model)
        GLib.idle_add(self._set_models, asr, llms)

    def _set_models(self, asr, llms):
        self.model_dd.set_model(Gtk.StringList.new(asr))
        self.model_dd.set_selected(asr.index(settings.whisper.model))
        self.llm_dd.set_model(Gtk.StringList.new(llms))
        self.llm_dd.set_selected(llms.index(settings.llm.model))
        return False

    def on_save(self, _btn):
        model = self.model_dd.get_selected_item().get_string()
        archive = (self.archive_dir.get_text().strip()
                   if self.archive_sw.get_active() else "off")
        cfg = {
            "model": model,
            "llm_model": self.llm_dd.get_selected_item().get_string(),
            "silence_ms": int(self.silence.get_value()),
            "idle_s": round(self.idle.get_value(), 1),
            "speak_answers": self.speak_sw.get_active(),
            "archive": archive,
        }
        for key, cb in self.notify_checks.items():
            cfg[f"notify_{key}"] = cb.get_active()
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for k, v in cfg.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {str(v).lower()}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            else:
                lines.append(f'{k} = "{v}"')
        CONFIG_PATH.write_text("\n".join(lines) + "\n")
        subprocess.run(["notify-send", "-a", "Dictate", "-t", "2500",
                        "Dictate", f"Settings saved ({model})"], check=False)
        self.close()


class MenuApp(Gtk.Application):
    def __init__(self, settings_only=False):
        super().__init__(application_id="io.github.rebreda.dictatr.menu")
        self.win = None
        self.settings_only = settings_only

    def do_activate(self):
        if self.settings_only:
            SettingsWindow(self).present()
            return
        # Single instance + toggle: a second hotkey press lands here in the
        # primary process; close the open menu instead of stacking another.
        if self.win is not None:
            self.win.dismiss()
            return
        self.win = Radial(self)
        self.win.connect("close-request", self._closed)
        self.win.present()

    def _closed(self, *_):
        self.win = None
        return False


def main():
    settings_only = "--settings" in sys.argv
    sys.exit(MenuApp(settings_only).run(
        [a for a in sys.argv if a != "--settings"]))


if __name__ == "__main__":
    main()
