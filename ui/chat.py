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
from dictatr import (actions, chatlog, concepts, llm,  # noqa: E402
                     mic, recall, runstate)
from dictatr.engine import dictate_once, ensure_asr_loaded  # noqa: E402
from dictatr.settings import settings  # noqa: E402
from dictatr.storage import save_recording  # noqa: E402

sys.path.insert(0, str(REPO / "ui"))
import radial  # noqa: E402
from radial import (BLUE, CHARCOAL, GREEN, INK, RED,  # noqa: E402
                    SIZE as RSIZE, Bubble, Ring)

WIDTH = 360
STACK_H = 620   # spacer + pills + status + hub; hub sits at the bottom

# Same visual vocabulary as the radial menu — the palette comes from the
# radial kit: round dark bubbles with thin white borders, blue hub mic,
# green record accent. No card, no chrome — message pills float on the
# transparent overlay, twirling out of a hub.
CSS = f"""
window {{ background: transparent; }}
.chat-entry, .chat-entry > text, .chat-entry > text > selection {{
  background: none;
  background-image: none;
  border: none;
  box-shadow: none;
  outline: none;
}}
.chat-entry {{
  background-color: alpha({CHARCOAL}, 0.93);
  border: 1px solid alpha(#ffffff, 0.10);
  border-radius: 9999px;
  padding: 8px 18px;
  min-height: 0;
  color: {INK};
  caret-color: {BLUE};
}}
.chat-entry:focus-within {{ border-color: alpha({BLUE}, 0.55); }}
.chat-entry > text > placeholder {{ color: alpha({INK}, 0.35); }}
/* The handle in a bubble's corner that opens its ring. */
.msgdot {{
  min-width: 18px; min-height: 18px; padding: 0;
  border-radius: 9999px;
  background: alpha({CHARCOAL}, 0.96);
  border: 1px solid alpha(#ffffff, 0.14);
  /* Faint but present: a handle nobody can see is a handle nobody
     uses, and hover-only affordances do not announce themselves. */
  opacity: 0.4;
  transition: opacity 120ms ease, border-color 120ms ease;
}}
.msgdot image {{ color: alpha({INK}, 0.75); -gtk-icon-size: 12px; }}
.msgdot:hover {{ opacity: 1; border-color: alpha({BLUE}, 0.6); }}
""".encode()


class Chat(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, decorated=False,
                         default_width=WIDTH)
        # Through the kit, not around it: this loads the ring stylesheet
        # and the bundled icons as well, which the rings opened over this
        # card need. Loading only our own sheet left them as plain GTK
        # buttons on a surface that is otherwise all bubbles.
        radial.apply_css(CSS)

        # Like the menu: a fullscreen transparent overlay so the card can
        # appear at the pointer. Unlike the menu, the input region is
        # clipped to the card, so the rest of the desktop stays clickable
        # while the conversation floats.

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
        self.suggest_btn = Gtk.Button(icon_name="starred-symbolic",
                                      valign=Gtk.Align.CENTER)
        self.suggest_btn.add_css_class("satbtn")
        self.suggest_btn.set_focusable(False)
        self.suggest_btn.set_tooltip_text("What to do with the answer")
        self.suggest_btn.set_visible(False)
        self.suggest_btn.connect("clicked", self.on_suggest_ring)

        hub_row.append(back)
        hub_row.append(self.mic_btn)
        hub_row.append(self.suggest_btn)
        hub_row.append(close)
        stack.append(hub_row)

        self.stack = stack
        self._hit_widgets = (self.scroll, status_row, entry_row, hub_row)
        # Clicks land only where the card is painted; the rest of the
        # desktop stays live under it. The hub sits at the bottom of the
        # stack, so that is what meets the pointer.
        self.ov = radial.Overlay(
            self, stack, (WIDTH, STACK_H),
            hit_widgets=lambda: (*self._hit_widgets,
                                 *( [self._ring] if self._ring else [] )),
            on_place=self._enter_from, clamp=12)
        stack.set_opacity(0.0)   # invisible until placed at the pointer
        self.overlay = self.ov.start()
        if self.overlay:
            self.canvas = self.ov.canvas
            self.ov.enable_drag(self._draggable_at)
        else:
            # No layer-shell: an ordinary window, and no canvas to put
            # rings on, so the per-message rings stay closed.
            self.canvas = None
            stack.set_opacity(1.0)
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
        # A screenshot the conversation is about, until it is asked about.
        self.shot = os.environ.get("DICTATE_SHOT") or None
        self._ring = None        # the radial currently over the card
        self._picks = {}         # text -> the model's shortlist for it
        self.log = chatlog.ChatLog()   # the conversation, kept
        self._turn_no = 0

        self.aio = asyncio.new_event_loop()
        threading.Thread(target=self.aio.run_forever, daemon=True).start()
        GLib.timeout_add(350, self._animate)

        if self.shot:
            GLib.idle_add(self._show_shot)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1,
                             self._commit_now)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM,
                             self._discard_now)
        self.connect("close-request", self.on_close)
        self.start_turn()

    # --- overlay -------------------------------------------------------
    def _enter_from(self, cx, cy):
        """Rise into place: the ring that opened this spiralled into its
        hub, and a card that simply blinks on breaks that thread."""
        start = self.get_frame_clock().get_frame_time() / 1e6

        def tick(_w, clock):
            p = min(1.0, (clock.get_frame_time() / 1e6 - start) / 0.22)
            e = 1 - (1 - p) ** 3
            self.stack.set_opacity(e)
            self.ov.canvas.move(self.stack, cx, cy + (1 - e) * 26)
            return p < 1.0

        self.add_tick_callback(tick)

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

    def _show_shot(self):
        """The screenshot as its own pill, so the conversation shows what
        it is about before a word is said."""
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN)
        pic.set_filename(self.shot)
        pic.set_size_request(-1, 150)
        wrap = Gtk.Box(halign=Gtk.Align.END)
        inner = Gtk.Box()
        inner.add_css_class("msg")
        inner.add_css_class("msg-user")
        inner.append(pic)
        wrap.append(inner)
        rev = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=200, child=wrap)
        self.msgs.append(rev)
        GLib.idle_add(rev.set_reveal_child, True)
        self._refade()
        self._scroll_down()
        self.set_status("screenshot attached: ask about it")
        self.log.turn("user", "(screenshot)", image=self.log.asset(self.shot))
        return False

    def bubble(self, role):
        lab = Gtk.Label(label="", wrap=True, xalign=0.0, selectable=True)
        lab.set_max_width_chars(36)
        inner = Gtk.Box()
        inner.add_css_class("msg")
        inner.add_css_class(f"msg-{role}")
        inner.append(lab)

        # A handle in the corner, which opens this message's own ring:
        # what to do with this line, rather than with the conversation.
        dot = Gtk.Button(icon_name="view-more-horizontal-symbolic",
                         valign=Gtk.Align.START, halign=Gtk.Align.START)
        dot.add_css_class("msgdot")
        dot.set_focusable(False)
        dot.set_tooltip_text("What to do with this")
        dot.connect("clicked", self.on_msg_menu, lab)

        overlay = Gtk.Overlay()
        overlay.set_child(inner)
        overlay.add_overlay(dot)
        overlay.add_css_class("msg-wrap")
        wrap = Gtk.Box(halign=Gtk.Align.END if role == "user"
                       else Gtk.Align.START)
        wrap.append(overlay)
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
        self.log.turn("user", text)
        self.phase = "thinking"
        self.set_status("thinking…")
        self.think_label = self.bubble("ai")
        asyncio.run_coroutine_threadsafe(self._answer(text, b""), self.aio)
        return False

    # --- rings over the conversation ----------------------------------
    def _open_ring(self, items, at_x, at_y, hub_tip="Close"):
        """Put a radial where the thing it acts on is."""
        if self.canvas is None or self._ring is not None:
            return
        ring = Ring(items, hub_icon="window-close-symbolic",
                    hub_tooltip=f"{hub_tip}  [Esc]",
                    on_root_hub=self._close_ring)
        x = min(max(at_x - RSIZE / 2, 4), max(self.get_width() - RSIZE - 4, 4))
        y = min(max(at_y - RSIZE / 2, 4), max(self.get_height() - RSIZE - 4, 4))
        self.canvas.put(ring, x, y)
        self._ring = ring
        ring.open()

    def _close_ring(self):
        ring, self._ring = self._ring, None
        if ring is not None:
            ring.dismiss(then=lambda: self.canvas.remove(ring))

    def _ring_items(self, text, picks):
        """Catalogue bubbles for one piece of text, plus copy."""
        items = [Bubble(p["icon"], p["label"],
                        self._ring_action(p, text)) for p in picks]
        items.append(Bubble("edit-copy-symbolic", "Copy",
                            self._copy(text)))
        return items

    def _ring_action(self, pick, text):
        def run():
            self._close_ring()
            self.run_action(pick, text)
        return run

    def _copy(self, text):
        def run():
            self._close_ring()
            subprocess.run(["wl-copy"], input=text.encode(), check=False)
            self.set_status("copied")
        return run

    def on_msg_menu(self, btn, lab):
        """The ring for one message: what to do with this line."""
        text = lab.get_label().strip()
        if not text or self._ring is not None:
            return
        ok, b = btn.compute_bounds(self)
        x = b.origin.x if ok else self.get_width() / 2
        y = b.origin.y if ok else self.get_height() / 2
        self._open_ring(self._ring_items(text, self._picks_for(text)), x, y,
                        "Close")
        threading.Thread(target=self._refresh_ring, args=(text,),
                         daemon=True).start()

    def _picks_for(self, text):
        """What the model last suggested for this text, or the staples
        until it answers: a ring must open now, not in two seconds."""
        cached = self._picks.get(text)
        if cached:
            return cached
        return [{"id": a.id, "arg": "", "label": a.label, "icon": a.icon}
                for a in actions.CATALOGUE[:3]]

    def _refresh_ring(self, text):
        try:
            picks = actions.suggest(text)
        except Exception:
            picks = []
        if picks:
            self._picks[text] = picks
            GLib.idle_add(self._swap_ring, text, picks)

    def _swap_ring(self, text, picks):
        if self._ring is not None:
            self._ring.swap(self._ring_items(text, picks),
                            hub_icon="window-close-symbolic",
                            hub_tooltip="Close  [Esc]")
        return False

    def on_suggest_ring(self, btn):
        """The ring for the conversation: what to do next, off the hub."""
        answer = self.history[-1]["content"] if self.history else ""
        if not answer or self._ring is not None:
            return
        ok, b = btn.compute_bounds(self)
        x = b.origin.x + (b.size.width / 2 if ok else 0)
        y = b.origin.y if ok else self.get_height() / 2
        self._open_ring(self._ring_items(answer, self._picks_for(answer)),
                        x, y - 40, "Close")
        threading.Thread(target=self._refresh_ring, args=(answer,),
                         daemon=True).start()

    def run_action(self, pick, text):
        """Run a catalogue action and show it as a turn, command and all,
        so the transcript says what was asked for and not only what came
        back."""
        if self.phase == "thinking":
            return
        asked = self.bubble("user")
        asked.set_label(pick["label"] + (f" ({pick['arg']})"
                                         if pick.get("arg") else ""))
        self.log.turn("action", pick["label"], action=pick["id"],
                      arg=pick.get("arg") or None, on=text[:400])
        self.phase = "thinking"
        self.set_status(f"{pick['label'].lower()}…")
        self.think_label = self.bubble("ai")
        asyncio.run_coroutine_threadsafe(
            self._run_action(pick, text), self.aio)

    async def _run_action(self, pick, text):
        try:
            out = await asyncio.to_thread(actions.run, pick["id"], text,
                                          pick.get("arg", ""))
        except Exception as e:
            GLib.idle_add(self._answer_failed, str(e))
            return
        self.history.append({"role": "assistant", "content": out})
        GLib.idle_add(self._show_answer, out)
        GLib.idle_add(self._turn_done)

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
        self.log.turn("user", text, spoken=True)
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
            shot, self.shot = self.shot, None   # asked about once
            answer = await asyncio.to_thread(
                llm.chat, text, context, list(self.history), shot)
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
        self.log.turn("assistant", answer)
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
        """Keep the model's shortlist for the answer and offer it on the
        hub, rather than spending a row of the card on buttons."""
        if self._closing or not picks:
            return False
        answer = self.history[-1]["content"] if self.history else ""
        if answer:
            self._picks[answer] = picks
            self.suggest_btn.set_visible(True)
        return False


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
