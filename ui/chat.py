#!/usr/bin/env python3
"""Floating voice chat for dictatr — the ask bubble grown into a sleek
conversation card.

Speak; your words stream into a bubble as the server transcribes them
(word-level deltas), the answer appears beneath, and the mic re-opens for
the follow-up — a continued conversation with history, entirely by voice.
Same visual family as the radial menu: dark translucent card, round mic
button, green record accent.

Coordinates with the rest of dictatr through runstate: while recording it
holds DICTATE_PID (the always-on listener pauses, the tray shows the
ask badge), the dictate hotkey commits the current utterance early, and
the cancel hotkey discards it. Esc or the close button dismisses.
"""

import asyncio
import contextlib
import signal
import subprocess
import sys
import threading
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from dictatr import concepts, llm, mic, recall, runstate  # noqa: E402
from dictatr.engine import dictate_once, ensure_asr_loaded  # noqa: E402
from dictatr.settings import settings  # noqa: E402
from dictatr.storage import save_recording  # noqa: E402

WIDTH = 380

CSS = b"""
window { background: transparent; }
.card {
  background: alpha(#17181d, 0.96);
  border: 1px solid alpha(#ffffff, 0.10);
  border-radius: 18px;
}
.header-title { color: #e8eaf1; font-weight: 600; }
.status { color: alpha(#e8eaf1, 0.55); font-size: 11px; }
.status.error { color: #f28b82; }
.bubble-user, .bubble-ai {
  border-radius: 14px; padding: 8px 12px;
}
.bubble-user {
  background: alpha(#81c995, 0.16);
  border: 1px solid alpha(#81c995, 0.35);
  color: #e8eaf1;
}
.bubble-user.live { border-color: alpha(#81c995, 0.8); }
.bubble-ai {
  background: #24262d;
  border: 1px solid alpha(#ffffff, 0.08);
  color: #e8eaf1;
}
.micbtn {
  border-radius: 9999px; min-width: 44px; min-height: 44px;
  background: alpha(#2a2c34, 0.95);
  border: 1px solid alpha(#ffffff, 0.12);
  transition: background 150ms ease, border-color 150ms ease;
}
.micbtn image { color: #e8eaf1; }
.micbtn.rec { background: alpha(#81c995, 0.30); border-color: #81c995; }
.micbtn.rec image { color: #81c995; }
.micbtn:hover { border-color: alpha(#ffffff, 0.35); }
.closebtn { border-radius: 9999px; min-width: 26px; min-height: 26px;
            background: transparent; border: none; }
.closebtn image { color: alpha(#e8eaf1, 0.5); }
.closebtn:hover image { color: #e8eaf1; }
"""


def layer_shell():
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LS
        # is_supported is false when the library isn't preloaded (see
        # bin/dictate-chat) or the compositor lacks the protocol (GNOME);
        # fall back to a normal floating window.
        return LS if LS.is_supported() else None
    except (ValueError, ImportError):
        return None


class Chat(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, decorated=False,
                         default_width=WIDTH)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # Like the menu: a fullscreen transparent overlay so the card can
        # appear at the pointer. Unlike the menu, the input region is
        # clipped to the card, so the rest of the desktop stays clickable
        # while the conversation floats.
        self.overlay = False
        ls = layer_shell()
        if ls is not None:
            self.overlay = True
            ls.init_for_window(self)
            ls.set_layer(self, ls.Layer.TOP)
            ls.set_keyboard_mode(self, ls.KeyboardMode.ON_DEMAND)
            for edge in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT,
                         ls.Edge.RIGHT):
                ls.set_anchor(self, edge, True)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("card")
        card.set_size_request(WIDTH, -1)

        header = Gtk.Box(spacing=8, margin_top=12, margin_start=16,
                         margin_end=10)
        title = Gtk.Label(label="Ask", xalign=0.0)
        title.add_css_class("header-title")
        self.status = Gtk.Label(label="", xalign=0.0, hexpand=True)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.add_css_class("status")
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.add_css_class("closebtn")
        close.connect("clicked", lambda *_: self.close())
        header.append(title)
        header.append(self.status)
        header.append(close)
        card.append(header)

        self.msgs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                            margin_start=12, margin_end=12)
        self.scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
            propagate_natural_height=True, max_content_height=440,
            min_content_height=140)
        self.scroll.set_child(self.msgs)
        card.append(self.scroll)

        footer = Gtk.Box(margin_bottom=12, margin_top=2, halign=Gtk.Align.CENTER)
        self.mic_btn = Gtk.Button(icon_name="audio-input-microphone-symbolic")
        self.mic_btn.add_css_class("micbtn")
        self.mic_btn.connect("clicked", self.on_mic)
        footer.append(self.mic_btn)
        card.append(footer)

        self.card = card
        if self.overlay:
            self.canvas = Gtk.Fixed()
            self.canvas.put(card, 0, 0)
            card.set_opacity(0.0)  # invisible until placed at the pointer
            self.placed = False
            self.set_child(self.canvas)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", self.on_pointer)
            motion.connect("motion", self.on_pointer)
            self.add_controller(motion)
            self._polls = 0
            GLib.timeout_add(50, self.poll_pointer)
            GLib.timeout_add(250, self._update_input_region)
        else:
            outer = Gtk.Box(margin_top=8, margin_bottom=8, margin_start=8,
                            margin_end=8)
            outer.append(card)
            self.set_child(outer)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        # --- session state ---
        self.history: list[dict] = []
        self.phase = "idle"      # idle | warmup | listening | thinking | speaking
        self.live_label = None   # streaming user bubble
        self.think_label = None
        self._discard = False
        self._warmed = False
        self._closing = False
        self._stop = None        # asyncio.Event of the live recording
        self._dots = 0

        self.aio = asyncio.new_event_loop()
        threading.Thread(target=self.aio.run_forever, daemon=True).start()
        GLib.timeout_add(350, self._animate)

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1,
                             self._commit_now)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM,
                             self._discard_now)
        self.connect("close-request", self.on_close)
        self.start_turn()

    # --- overlay placement (menu-style, card at the pointer) ----------
    def on_pointer(self, _c, x, y):
        self.place_at(x, y)

    def poll_pointer(self):
        if self.placed or not self.overlay:
            return False
        surface = self.get_surface()
        if surface is not None and self.get_width() > 0:
            seat = Gdk.Display.get_default().get_default_seat()
            ok, x, y, _m = surface.get_device_position(seat.get_pointer())
            if ok and (x or y):
                self.place_at(x, y)
                return False
        self._polls += 1
        if self._polls > 20 and self.get_width() > 0:
            self.place_at(self.get_width() / 2, self.get_height() / 3)
            return False
        return True

    def place_at(self, x, y):
        if self.placed or not self.overlay:
            return
        self.placed = True
        w = self.get_width() or 1920
        h = self.get_height() or 1080
        # Card top near the pointer; it grows downward. Clamp inside the
        # screen, leaving room for a full conversation below.
        cx = min(max(x - WIDTH / 2, 12), max(w - WIDTH - 12, 12))
        cy = min(max(y - 24, 12), max(h - 640, 12))
        self.canvas.move(self.card, cx, cy)
        self.card.set_opacity(1.0)

    def _update_input_region(self):
        """Clip input to the card: clicks elsewhere fall through to
        whatever is underneath, so the overlay never blocks the desktop."""
        if self._closing:
            return False
        surface = self.get_surface()
        if surface is None or not self.placed:
            return True
        ok, bounds = self.card.compute_bounds(self)
        if ok:
            rect = cairo.RectangleInt(int(bounds.origin.x) - 2,
                                      int(bounds.origin.y) - 2,
                                      int(bounds.size.width) + 4,
                                      int(bounds.size.height) + 4)
            surface.set_input_region(cairo.Region(rect))
        return True

    # --- animation & status -------------------------------------------
    def _animate(self):
        self._dots = (self._dots + 1) % 4
        dots = "·" * (self._dots or 1)
        if self.phase == "thinking" and self.think_label is not None:
            self.think_label.set_label(dots)
        elif self.phase == "listening" and self.live_label is not None \
                and not self.live_label.get_label().strip("▏ "):
            self.live_label.set_label("▏" if self._dots % 2 else " ▏")
        return not self._closing

    def set_status(self, text, error=False):
        self.status.set_label(text)
        (self.status.add_css_class if error
         else self.status.remove_css_class)("error")

    def _scroll_down(self):
        adj = self.scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) and False)

    def bubble(self, role):
        lab = Gtk.Label(label="", wrap=True, xalign=0.0, selectable=True)
        lab.set_max_width_chars(36)
        wrap = Gtk.Box(halign=Gtk.Align.END if role == "user"
                       else Gtk.Align.START)
        inner = Gtk.Box()
        inner.add_css_class(f"bubble-{role}")
        inner.append(lab)
        wrap.append(inner)
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                           transition_duration=200, child=wrap)
        self.msgs.append(rev)
        GLib.idle_add(rev.set_reveal_child, True)
        self._scroll_down()
        lab._inner = inner
        lab._rev = rev
        return lab

    def drop_bubble(self, lab):
        if lab is not None:
            self.msgs.remove(lab._rev)

    # --- turn flow (UI side; work happens on the asyncio thread) -------
    def start_turn(self):
        if self.phase != "idle" or self._closing:
            return
        self.phase = "listening"
        self._discard = False
        self.mic_btn.add_css_class("rec")
        self.live_label = self.bubble("user")
        self.live_label._inner.add_css_class("live")
        self.set_status("warming up…" if not self._warmed else
                        "listening — just talk")
        asyncio.run_coroutine_threadsafe(self._turn(), self.aio)

    def on_mic(self, _btn):
        if self.phase == "listening":
            self._commit_now()
        elif self.phase == "idle":
            self.start_turn()

    def on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _commit_now(self):
        if self._stop is not None:
            self.aio.call_soon_threadsafe(self._stop.set)
        return True  # keep the signal handler installed

    def _discard_now(self):
        self._discard = True
        self._commit_now()
        return True

    def on_close(self, *_):
        self._closing = True
        self._discard = True
        self._commit_now()
        runstate.DICTATE_PID.unlink(missing_ok=True)
        runstate.MODE.unlink(missing_ok=True)
        return False

    # --- the actual work ----------------------------------------------
    async def _turn(self):
        stop = asyncio.Event()
        self._stop = stop
        runstate.write_pid(runstate.DICTATE_PID)
        runstate.write_mode("ask")
        text, pcm = None, b""
        try:
            if not self._warmed:
                await asyncio.to_thread(ensure_asr_loaded)
                self._warmed = True
                GLib.idle_add(self.set_status, "listening — just talk")
            text, pcm = await dictate_once(
                mic.mic_chunks(stop), stop,
                on_partial=lambda t: GLib.idle_add(self._on_partial, t))
        except (ConnectionError, OSError, RuntimeError) as e:
            GLib.idle_add(self._turn_failed, f"Lemonade offline — {e}")
            return
        finally:
            self._stop = None
            runstate.DICTATE_PID.unlink(missing_ok=True)
            runstate.MODE.unlink(missing_ok=True)
        GLib.idle_add(self._got_text, text or "", pcm)

    def _on_partial(self, text):
        if self.live_label is not None:
            self.live_label.set_label(text)
            self._scroll_down()

    def _turn_failed(self, msg):
        self.phase = "idle"
        self.mic_btn.remove_css_class("rec")
        self.drop_bubble(self.live_label)
        self.live_label = None
        self.set_status(msg + " — tap the mic to retry", error=True)

    def _got_text(self, text, pcm):
        self.mic_btn.remove_css_class("rec")
        if self._closing or self._discard or not text.strip():
            self.drop_bubble(self.live_label)
            self.live_label = None
            self.phase = "idle"
            if not self._closing:
                self.set_status("tap the mic to talk")
            return
        self.live_label._inner.remove_css_class("live")
        self.live_label.set_label(text)
        self.live_label = None
        self.phase = "thinking"
        self.set_status("thinking…")
        self.think_label = self.bubble("ai")
        asyncio.run_coroutine_threadsafe(self._answer(text, pcm), self.aio)

    async def _answer(self, text, pcm):
        context = []
        if settings.llm.recall and settings.storage.enabled:
            with contextlib.suppress(OSError):
                context = await asyncio.to_thread(recall.search, text)
        try:
            answer = await asyncio.to_thread(
                llm.chat, text, context, list(self.history))
        except OSError as e:
            GLib.idle_add(self._answer_failed, str(e))
            return
        self.history += [{"role": "user", "content": text},
                         {"role": "assistant", "content": answer}]
        GLib.idle_add(self._show_answer, answer)

        if settings.storage.enabled and pcm:
            with contextlib.suppress(OSError, ValueError):
                record = save_recording(
                    pcm, text,
                    storage_base=Path(settings.storage.base).expanduser(),
                    whisper_model=settings.whisper.model,
                    meta={"mode": "ask"})
                if settings.llm.concepts:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(concepts.annotate, record)
        if settings.llm.speak and not self._closing:
            GLib.idle_add(self.set_status, "speaking…")
            await asyncio.to_thread(llm.speak, answer)
        GLib.idle_add(self._turn_done)

    def _answer_failed(self, msg):
        self.phase = "idle"
        self.drop_bubble(self.think_label)
        self.think_label = None
        self.set_status(f"LLM unreachable — {msg}", error=True)

    def _show_answer(self, answer):
        subprocess.run(["wl-copy"], input=answer.encode(), check=False)
        if self.think_label is not None:
            self.think_label.set_label(answer)
            self.think_label = None
        self._scroll_down()

    def _turn_done(self):
        self.phase = "idle"
        if not self._closing:
            self.start_turn()  # keep the conversation going


class ChatApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.rebreda.dictatr.chat")
        self.win = None

    def do_activate(self):
        if self.win is None:
            self.win = Chat(self)
            self.win.connect("close-request",
                             lambda *_: setattr(self, "win", None) or False)
        self.win.present()


if __name__ == "__main__":
    sys.exit(ChatApp().run(sys.argv))
