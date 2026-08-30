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
import os
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
from dictatr import actions, concepts, llm, mic, recall, runstate  # noqa: E402
from dictatr.engine import dictate_once, ensure_asr_loaded  # noqa: E402
from dictatr.settings import settings  # noqa: E402
from dictatr.storage import save_recording  # noqa: E402

sys.path.insert(0, str(REPO / "ui"))
import radial  # noqa: E402
from radial import BLUE, CHARCOAL, GREEN, INK, RED  # noqa: E402

WIDTH = 360
STACK_H = 620   # spacer + pills + status + hub; hub sits at the bottom

# Same visual vocabulary as the radial menu — the palette comes from the
# radial kit: round dark bubbles with thin white borders, blue hub mic,
# green record accent. No card, no chrome — message pills float on the
# transparent overlay, twirling out of a hub.
CSS = f"""
window {{ background: transparent; }}
.hubbtn {{
  border-radius: 9999px;
  border: 1px solid alpha(#ffffff, 0.10);
  background: alpha({CHARCOAL}, 0.93);
  min-width: 58px; min-height: 58px;
  transition: background 150ms ease, border-color 150ms ease;
}}
.hubbtn image {{ color: {BLUE}; }}
.hubbtn.rec {{ background: alpha({GREEN}, 0.25); border-color: alpha({GREEN}, 0.6); }}
.hubbtn.rec image {{ color: {GREEN}; }}
.hubbtn:hover {{ border-color: alpha(#ffffff, 0.35); }}
.satbtn {{
  border-radius: 9999px;
  border: 1px solid alpha(#ffffff, 0.10);
  background: alpha({CHARCOAL}, 0.93);
  min-width: 36px; min-height: 36px;
}}
.satbtn image {{ color: {INK}; }}
.satbtn:hover {{ background: alpha({RED}, 0.25); border-color: alpha({RED}, 0.6); }}
.satbtn.back:hover {{ background: alpha({BLUE}, 0.25); border-color: alpha({BLUE}, 0.6); }}
.msg {{
  border-radius: 20px; padding: 9px 14px;
  background: alpha({CHARCOAL}, 0.93);
  border: 1px solid alpha(#ffffff, 0.10);
  color: {INK};
  transition: border-color 150ms ease;
}}
.msg-user {{ border-color: alpha({GREEN}, 0.45); }}
.msg-user.live {{ border-color: alpha({GREEN}, 0.85); }}
.msg-ai {{ border-color: alpha({BLUE}, 0.35); }}
.status-pill {{
  background: alpha({CHARCOAL}, 0.85);
  border: 1px solid alpha(#ffffff, 0.08);
  border-radius: 9999px; padding: 3px 12px;
  color: alpha({INK}, 0.6); font-size: 11px;
}}
.status-pill.error {{ color: {RED}; }}
""".encode()


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
        ls = radial.layer_shell()
        if ls is not None:
            self.overlay = True
            ls.init_for_window(self)
            ls.set_layer(self, ls.Layer.TOP)
            ls.set_keyboard_mode(self, ls.KeyboardMode.ON_DEMAND)
            for edge in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT,
                         ls.Edge.RIGHT):
                ls.set_anchor(self, edge, True)

        # A column that grows upward from the hub: spacer, message pills,
        # status pill, then the hub row (mic bubble + close satellite) —
        # the hub lands where the pointer was, like the menu's center.
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        stack.set_size_request(WIDTH, STACK_H)
        stack.append(Gtk.Box(vexpand=True))  # pushes everything down

        self.msgs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, max_content_height=430,
            valign=Gtk.Align.END)
        self.scroll.set_child(self.msgs)
        stack.append(self.scroll)

        self.follow = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.follow.set_visible(False)
        self._follow_row = []
        stack.append(self.follow)

        self.status = Gtk.Label(label="")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.add_css_class("status-pill")
        status_row = Gtk.Box(halign=Gtk.Align.CENTER)
        status_row.append(self.status)
        stack.append(status_row)

        # Typing is the quiet option: same conversation, no microphone,
        # for when the answer is easier written than said (a path, a
        # name) or the room is not yours to talk in.
        self.entry = Gtk.Entry(placeholder_text="or type…",
                               halign=Gtk.Align.CENTER)
        self.entry.add_css_class("chat-entry")
        self.entry.set_size_request(WIDTH - 90, -1)
        self.entry.connect("activate", self.on_entry)
        entry_row = Gtk.Box(halign=Gtk.Align.CENTER)
        entry_row.append(self.entry)
        stack.append(entry_row)

        hub_row = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        back = Gtk.Button(icon_name="go-previous-symbolic",
                          valign=Gtk.Align.CENTER,
                          tooltip_text="Back to the menu")
        back.add_css_class("satbtn")
        back.add_css_class("back")
        back.connect("clicked", self.on_back)
        self.mic_btn = Gtk.Button(icon_name="audio-input-microphone-symbolic")
        self.mic_btn.add_css_class("hubbtn")
        self.mic_btn.connect("clicked", self.on_mic)
        close = Gtk.Button(icon_name="window-close-symbolic",
                           valign=Gtk.Align.CENTER)
        close.add_css_class("satbtn")
        close.connect("clicked", lambda *_: self.close())
        hub_row.append(back)
        hub_row.append(self.mic_btn)
        hub_row.append(close)
        stack.append(hub_row)

        self.stack = stack
        self._hit_widgets = (self.scroll, self.follow, status_row,
                             entry_row, hub_row)
        if self.overlay:
            self.canvas = Gtk.Fixed()
            self.canvas.put(stack, 0, 0)
            stack.set_opacity(0.0)  # invisible until placed at the pointer
            self.placed = False
            self.set_child(self.canvas)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", self.on_pointer)
            motion.connect("motion", self.on_pointer)
            self.add_controller(motion)
            self._polls = 0
            GLib.timeout_add(50, self.poll_pointer)
            GLib.timeout_add(250, self._update_input_region)

            # Drag the mic hub to move the conversation; small movements
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
            outer = Gtk.Box(margin_top=8, margin_bottom=8, margin_start=8,
                            margin_end=8)
            outer.append(stack)
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
        self._turn_no = 0

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
            # No pointer position to be had: settle low-center — the hub
            # anchors the column's bottom, so the conversation needs its
            # room *above* the fallback point, not below.
            self.place_at(self.get_width() / 2, self.get_height() * 0.62)
            return False
        return True

    def place_at(self, x, y):
        if self.placed or not self.overlay:
            return
        self.placed = True
        w = self.get_width() or 1920
        h = self.get_height() or 1080
        # The hub (bottom of the stack) lands at the pointer, like the
        # menu's center bubble; pills grow upward from there.
        cx = min(max(x - WIDTH / 2, 12), max(w - WIDTH - 12, 12))
        cy = min(max(y - STACK_H + 34, 12 - STACK_H + 120),
                 max(h - STACK_H - 12, 12))
        self._pos = (cx, cy)
        self.canvas.move(self.stack, cx, cy)
        self._enter_from(cx, cy)

    ENTER_S = 0.22

    def _enter_from(self, cx, cy):
        """Rise into place: the ring that opened this spiralled into its
        hub, and a card that simply blinks on breaks that thread."""
        start = self.get_frame_clock().get_frame_time() / 1e6

        def tick(_w, clock):
            p = min(1.0, (clock.get_frame_time() / 1e6 - start) / self.ENTER_S)
            e = 1 - (1 - p) ** 3
            self.stack.set_opacity(e)
            self.canvas.move(self.stack, cx, cy + (1 - e) * 26)
            return p < 1.0

        self.stack.set_opacity(0.0)
        self.add_tick_callback(tick)

    def _update_input_region(self):
        """Clip input to the visible pieces (pills, status, hub): clicks
        anywhere else fall through to whatever is underneath, so the
        overlay never blocks the desktop."""
        if self._closing:
            return False
        surface = self.get_surface()
        if surface is None or not self.placed:
            return True
        region = cairo.Region()
        for widget in self._hit_widgets:
            ok, b = widget.compute_bounds(self)
            if ok and b.size.width > 0:
                region.union(cairo.RectangleInt(
                    int(b.origin.x) - 4, int(b.origin.y) - 4,
                    int(b.size.width) + 8, int(b.size.height) + 8))
        surface.set_input_region(region)
        return True

    # --- animation & status -------------------------------------------
    def _animate(self):
        self._dots = (self._dots + 1) % 3
        if self.phase == "thinking" and self.think_label is not None:
            self.think_label.set_label("· " * (self._dots + 1))
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
        inner.add_css_class("msg")
        inner.add_css_class(f"msg-{role}")
        inner.append(lab)
        wrap.append(inner)
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                           transition_duration=200, child=wrap)
        self.msgs.append(rev)
        GLib.idle_add(rev.set_reveal_child, True)
        self._refade()
        self._scroll_down()
        lab._inner = inner
        lab._rev = rev
        return lab

    def drop_bubble(self, lab):
        if lab is not None:
            self.msgs.remove(lab._rev)
            self._refade()

    def _refade(self):
        """Older pills fade with age: the freshest exchange is solid, the
        history dims stepwise behind it."""
        pills = []
        child = self.msgs.get_first_child()
        while child is not None:
            pills.append(child)
            child = child.get_next_sibling()
        n = len(pills)
        for i, rev in enumerate(pills):
            age = n - 1 - i  # 0 = newest
            rev.set_opacity(1.0 if age < 2 else max(0.35, 1.0 - 0.16 * age))

    # --- turn flow (UI side; work happens on the asyncio thread) -------
    def start_turn(self):
        if self.phase != "idle" or self._closing:
            return
        self.phase = "listening"
        self._discard = False
        self.mic_btn.add_css_class("rec")
        # No empty pill while waiting: the green hub says "listening";
        # the pill appears with the first words.
        self.live_label = None
        self.set_status("warming up…" if not self._warmed else
                        "listening — just talk")
        asyncio.run_coroutine_threadsafe(self._turn(), self.aio)

    def _draggable_at(self, x, y):
        """Anywhere on the card that is not a control moves it: the hub
        alone was a 58px target for repositioning a conversation."""
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        on_card = False
        while target is not None:
            if isinstance(target, (Gtk.Button, Gtk.Entry)):
                return False
            if target is self.stack:
                on_card = True
            target = target.get_parent()
        return on_card

    def on_drag_begin(self, _g, x, y):
        self._drag_from = self._pos if self._draggable_at(x, y) else None

    def on_drag_update(self, g, dx, dy):
        if self._drag_from is None:
            return
        if abs(dx) > 8 or abs(dy) > 8:
            g.set_state(Gtk.EventSequenceState.CLAIMED)
        self.canvas.move(self.stack,
                         self._drag_from[0] + dx, self._drag_from[1] + dy)

    def on_drag_end(self, _g, dx, dy):
        if self._drag_from is not None:
            self._pos = (self._drag_from[0] + dx, self._drag_from[1] + dy)
            self._drag_from = None

    def on_entry(self, entry):
        text = entry.get_text().strip()
        if not text:
            return
        entry.set_text("")
        # The mic was listening; this turn is typed, so drop whatever it
        # had rather than letting the two interleave.
        self._discard = True
        self._commit_now()
        GLib.timeout_add(180, self._typed_turn, text)

    def _typed_turn(self, text):
        if self._closing:
            return False
        self.mic_btn.remove_css_class("rec")
        self.drop_bubble(self.live_label)
        self.live_label = self.bubble("user")
        self.live_label.set_label(text)
        self.live_label = None
        self.phase = "thinking"
        self.set_status("thinking…")
        self.think_label = self.bubble("ai")
        asyncio.run_coroutine_threadsafe(self._answer(text, b""), self.aio)
        return False

    def on_back(self, _btn):
        subprocess.Popen([str(REPO / "bin" / "dictate-menu")])
        self.close()

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
    def _audio_source(self, stop):
        """The mic, or — like the CLI and the tests — canned audio via
        DICTATE_INPUT: a colon-separated wav list consumed one per turn
        (silence once exhausted)."""
        if not settings.input_file:
            return mic.mic_chunks(stop)
        paths = [p for p in settings.input_file.split(":") if p]
        turn, self._turn_no = self._turn_no, self._turn_no + 1
        if turn >= len(paths):
            async def silence():
                return
                yield
            return silence()
        return mic.file_chunks(
            paths[turn], stop,
            realtime=os.environ.get("DICTATE_INPUT_PACED") == "1")

    async def _turn(self):
        stop = asyncio.Event()
        self._stop = stop
        runstate.write_pid(runstate.DICTATE_PID)
        runstate.write_mode("ask")
        text, pcm = None, b""
        try:
            if not settings.input_file and \
                    await asyncio.to_thread(mic.source_muted):
                GLib.idle_add(self._turn_failed,
                              "microphone is muted — unmute, then tap")
                return
            if not self._warmed:
                await asyncio.to_thread(ensure_asr_loaded)
                self._warmed = True
                GLib.idle_add(self.set_status, "listening — just talk")
            text, pcm = await dictate_once(
                self._audio_source(stop), stop,
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
        """Show the words in the input as they are recognised.

        The same place typed words would go, so speaking and typing feed
        one field instead of two competing displays, and a wrong word is
        visible before it is sent."""
        self.entry.set_text(text)
        self.entry.set_position(-1)

    def _turn_failed(self, msg):
        self.phase = "idle"
        self.mic_btn.remove_css_class("rec")
        self.drop_bubble(self.live_label)
        self.live_label = None
        self.set_status(msg + " — tap the mic to retry", error=True)

    def _got_text(self, text, pcm):
        self.mic_btn.remove_css_class("rec")
        self.entry.set_text("")
        if self._closing or self._discard or not text.strip():
            self.drop_bubble(self.live_label)
            self.live_label = None
            self.phase = "idle"
            if self._closing:
                return
            if self._discard:
                self.set_status("cancelled — tap the mic to talk")
            else:
                self.start_turn()  # quiet spell: just keep listening
            return
        if self.live_label is None:
            self.live_label = self.bubble("user")
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
        threading.Thread(target=self._fetch_follow_ups, args=(answer,),
                         daemon=True).start()

    def _fetch_follow_ups(self, answer):
        try:
            picks = actions.suggest(answer)
        except Exception:
            picks = []
        GLib.idle_add(self._show_follow_ups, picks)

    def _show_follow_ups(self, picks):
        """What to do with the answer, as a row of small round buttons.

        The same catalogue the selection ring offers, pointed at what the
        model just said: an answer is usually a step, not an ending."""
        for child in list(self._follow_row):
            self.follow.remove(child)
        self._follow_row.clear()
        if self._closing or not picks:
            self.follow.set_visible(False)
            return False
        for p in picks[:3]:
            b = Gtk.Button(icon_name=p["icon"], tooltip_text=p["label"])
            b.add_css_class("satbtn")
            b.connect("clicked", self.on_follow_up, p)
            self.follow.append(b)
            self._follow_row.append(b)
        self.follow.set_visible(True)
        return False

    def on_follow_up(self, _btn, pick):
        """Run a catalogue action on the answer and show it as a turn."""
        answer = self.history[-1]["content"] if self.history else ""
        if not answer:
            return
        self.follow.set_visible(False)
        self.phase = "thinking"
        self.set_status(f"{pick['label'].lower()}…")
        self.think_label = self.bubble("ai")
        asyncio.run_coroutine_threadsafe(
            self._run_follow_up(pick, answer), self.aio)

    async def _run_follow_up(self, pick, answer):
        try:
            out = await asyncio.to_thread(actions.run, pick["id"], answer,
                                          pick["arg"])
        except Exception as e:
            GLib.idle_add(self._answer_failed, str(e))
            return
        self.history.append({"role": "assistant", "content": out})
        GLib.idle_add(self._show_answer, out)
        GLib.idle_add(self._turn_done)

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
