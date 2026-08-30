#!/usr/bin/python3
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
gi.require_version("GLibUnix", "2.0")
from gi.repository import (Gdk, GLib, GLibUnix,  # noqa: E402
                           Gtk, Pango)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from dictatr import (actions, chatlog, concepts, llm,  # noqa: E402
                     mic, recall, runstate)
from dictatr.engine import dictate_once, ensure_asr_loaded  # noqa: E402
from dictatr.settings import settings, write_config  # noqa: E402
from dictatr.storage import save_recording  # noqa: E402

sys.path.insert(0, str(REPO / "ui"))
import handoff  # noqa: E402
import motion  # noqa: E402
import radial  # noqa: E402
from radial import (BLUE, CHARCOAL, INK, STYLE_CARD,  # noqa: E402
                    Bubble, Hub, Ring)

WIDTH = 360
STACK_H = 620   # spacer + pills + status + entry + room for the ring

# What the card's ring takes up when nothing is open below it: the
# column reserves that much and no more, so a submenu is free to grow
# past the card instead of shoving the conversation around.
RING_H = 2 * (STYLE_CARD.radius + STYLE_CARD.bubble / 2)
HUB_Y = STACK_H - RING_H / 2      # the hub's centre, within the column

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
/* The working behind an answer: present but subordinate to it, so a
   card with details on still reads as a conversation and not as a log. */
.msg-trace {{
  border-color: alpha(#ffffff, 0.08);
  background: alpha({CHARCOAL}, 0.75);
  color: alpha({INK}, 0.60);
  font-size: 11px;
  padding: 7px 12px;
}}
""".encode()


class Chat(Gtk.ApplicationWindow):
    # Set before the column is built, because the ring can report how
    # much room it wants while the card is still being assembled.
    _closing = False

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

        self._status = ("", False)
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
        # Digits belong to the ring while the field is empty, and to the
        # field once it is not. Capture phase because the entry would
        # otherwise consume them before anything else is offered them.
        entry_keys = Gtk.EventControllerKey()
        entry_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        entry_keys.connect("key-pressed", self._entry_key)
        self.entry.add_controller(entry_keys)
        entry_row = Gtk.Box(halign=Gtk.Align.CENTER)
        entry_row.append(self.entry)
        stack.append(entry_row)

        # Room for the ring, which is not in this column: it hangs off
        # the card. The gap grows when the ring does, so opening a level
        # lifts the conversation clear of it instead of letting the new
        # bubbles be drawn over the card that spawned them.
        self._gap = RING_H
        self.ring_gap = Gtk.Box(height_request=round(RING_H))
        stack.append(self.ring_gap)

        # The card's controls are a ring like every other ring in the
        # family, with the microphone as its hub. They were a bespoke
        # widget of their own, which is a whole second way of laying out
        # a hub and its satellites for four buttons that do nothing a
        # bubble cannot. Two groups: leaving the card, then acting on
        # it, and the layout leaves more room between the pairs than
        # inside them.
        self.mic_btn = Gtk.Button()
        self.mic_btn.add_css_class("hubbtn")
        self.ring = Ring(
            self._chrome_items(), style=STYLE_CARD,
            hub=Hub("audio-input-microphone-symbolic", "Talk",
                    action=self.on_mic, widget=self.mic_btn, keep=True),
            obstacles=self._in_the_way, on_geometry=self._ring_grew)

        self.stack = stack
        self._hit_widgets = (self.scroll, status_row, entry_row)
        # Clicks land only where the card is painted; the rest of the
        # desktop stays live under it, and the ring reports its bubbles
        # one by one rather than as the square it is drawn in.
        self.ov = radial.Overlay(
            self, stack, (WIDTH, STACK_H), hit_widgets=self._hits,
            on_place=self._enter_from, clamp=12,
            anchor=(0.5, HUB_Y / STACK_H))
        stack.set_opacity(0.0)   # invisible until placed at the pointer
        self.overlay = self.ov.start()
        if self.overlay:
            self.canvas = self.ov.canvas
            self.ov.add_floating(self.ring, (WIDTH / 2 - self.ring.size / 2,
                                             HUB_Y - self.ring.size / 2))
            self.ov.enable_drag(self._draggable_at)
        else:
            # No layer-shell: an ordinary window. The ring goes in the
            # column instead of beside it, which costs it the room to
            # grow but keeps every surface working on GNOME.
            self.canvas = None
            stack.set_opacity(1.0)
            stack.append(self.ring)
            outer = Gtk.Box(margin_top=8, margin_bottom=8, margin_start=8,
                            margin_end=8)
            outer.append(stack)
            self.set_child(outer)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        # --- session state ---
        self.history: list[dict] = []
        self._phase = None
        self.phase = "idle"      # idle | listening | thinking
        self.live_label = None   # streaming user bubble
        self.think_label = None
        self._discard = False
        self._warmed = False
        self._closing = False
        self._stop = None        # asyncio.Event of the live recording
        self._dots = 0
        # A screenshot the conversation is about, until it is asked about.
        self.shot = os.environ.get("DICTATE_SHOT") or None
        self._picks = {}         # text -> the model's shortlist for it
        self._acting_on = None   # the text the open action level is about
        self.log = chatlog.ChatLog()   # the conversation, kept
        self._turn_no = 0

        self.aio = asyncio.new_event_loop()
        threading.Thread(target=self.aio.run_forever, daemon=True).start()
        GLib.timeout_add(350, self._animate)

        if self.shot:
            GLib.idle_add(self._show_shot)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1,
                             self._commit_now)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM,
                             self._discard_now)
        self.connect("close-request", self.on_close)
        self.start_turn()

    # --- the hub ------------------------------------------------------
    # What the one big button is for, per phase. It was a microphone
    # whatever the card was doing, so the only way to find out what
    # pressing it did was to press it.
    MIC_FACES = {
        "idle": ("audio-input-microphone-symbolic", "Talk", True),
        "listening": ("media-playback-stop-symbolic",
                      "Send now — stop listening", True),
        "thinking": ("audio-input-microphone-symbolic", "Answering…", False),
    }

    @property
    def phase(self):
        return self._phase

    @phase.setter
    def phase(self, value):
        """One place decides what the hub looks like, because every
        branch that changed the phase used to also have to remember to
        change the button, and they did not all remember."""
        self._phase = value
        icon, tip, live = self.MIC_FACES.get(value, self.MIC_FACES["idle"])
        self.ring.set_hub(icon=icon, tooltip=tip, sensitive=live,
                          css=("rec",) if value == "listening" else ())

    # --- overlay -------------------------------------------------------
    def _enter_from(self, cx, cy):
        """Rise into place, then bloom.

        One timeline rather than a tick callback and an unrelated
        timeout whose numbers happened to add up: the column settles,
        and a beat later the ring twirls out of its hub, picking up the
        motion of the ring that spiralled into it to open this card. By
        then the column has been allocated, which is also the first
        moment the arc can be solved against what is above it.
        """
        arrival = motion.Timeline(
            alpha=motion.Track(0.0, 1.0, 0.22),
            lift=motion.Track(26.0, 0.0, 0.22),
            # A cue, not a value: it flips a beat in and opens the ring.
            bloom=motion.Track(0.0, 1.0, 0.001, delay=0.12))
        bloomed = [False]

        def apply(v):
            self.stack.set_opacity(v["alpha"])
            # Through the overlay, so the ring hanging off the card
            # rises with it instead of sitting at its final spot while
            # the card climbs past.
            self.ov.move(cx, cy + v["lift"])
            if v["bloom"] > 0.5 and not bloomed[0]:
                bloomed[0] = True
                self._open_ring()
                # Typing needs no click. Layer-shell keyboard mode is
                # ON_DEMAND, so the compositor hands this surface the
                # keyboard only once something inside it holds focus,
                # and nothing ever did: the field looked ready, showed
                # no caret, and swallowed everything typed at it. The
                # numbered bubbles survive it -- see _entry_key.
                self.entry.grab_focus()

        radial.play(self, arrival, apply)

    def _draggable_at(self, x, y):
        """Anywhere on the card that is not a control moves it: the hub
        alone was a 58px target for repositioning a conversation."""
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        on_card = False
        while target is not None:
            if isinstance(target, (Gtk.Button, Gtk.Entry)):
                return False
            # The ring hangs off the card rather than sitting in it, so
            # the empty circle around the hub has to count as the card
            # too — it is most of what is under the pointer down there.
            if target is self.stack or target is self.ring:
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
        """Crossfade the status line.

        Its text and its width both change, and it is centred, so a hard
        swap moves the pill sideways under the eye that is reading it.
        The pending text is held on self, so two statuses in quick
        succession land on the later one rather than on whichever
        animation happened to survive.
        """
        self._status = (text, error)

        def apply():
            label, bad = self._status
            self.status.set_label(label)
            (self.status.add_css_class if bad
             else self.status.remove_css_class)("error")

        if self.status.get_label() == text:
            apply()
        else:
            radial.crossfade(self.status, apply)

    def _scroll_down(self):
        adj = self.scroll.get_vadjustment()
        # On idle, so the new pill has been allocated and the target is
        # the real bottom rather than the one from before it landed.
        GLib.idle_add(lambda: (radial.scroll_to(
            self.scroll, adj, adj.get_upper() - adj.get_page_size()),
            False)[1])

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

    TRACE_MAX = 10       # lines; a long tool loop is a summary, not a log
    TRACE_CHARS = 110    # per line, before the tail is cut

    def trace_bubble(self, steps):
        """The working behind the last answer, as its own quiet pill.

        Deliberately not a bubble(): it carries no dot handle, because
        there is nothing to do with a trace except read it."""
        lines = []
        for st in steps[:self.TRACE_MAX]:
            detail = " ".join((st["detail"] or "").split())
            if len(detail) > self.TRACE_CHARS:
                detail = detail[:self.TRACE_CHARS - 1] + "…"
            # Pango markup, not CSS: a label's spans are styled by
            # attribute, and a stylesheet cannot reach inside one.
            lines.append(
                f'<span foreground="{BLUE}" weight="bold">'
                f'{GLib.markup_escape_text(st["kind"])}</span>  '
                f'{GLib.markup_escape_text(detail)}')
        if len(steps) > self.TRACE_MAX:
            lines.append(f"… and {len(steps) - self.TRACE_MAX} more")

        lab = Gtk.Label(wrap=True, xalign=0.0, selectable=True)
        lab.set_max_width_chars(40)
        lab.set_markup("\n".join(lines))
        inner = Gtk.Box()
        inner.add_css_class("msg")
        inner.add_css_class("msg-trace")
        inner.append(lab)
        wrap = Gtk.Box(halign=Gtk.Align.START)
        wrap.append(inner)
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                           transition_duration=200, child=wrap)
        self.msgs.append(rev)
        GLib.idle_add(rev.set_reveal_child, True)
        self._refade()
        self._scroll_down()

    def _grow_into(self, lab, text):
        """Put text in a pill and let the pill grow to hold it.

        A one-line "· · ·" becoming a paragraph in a single frame shoves
        the whole column upward; easing the height turns the same event
        into the answer arriving.
        """
        wrap = lab._inner
        was = wrap.get_height()
        lab.set_label(text)
        if was <= 0:
            return
        _, want, _, _ = wrap.measure(Gtk.Orientation.VERTICAL,
                                     wrap.get_width())
        if want > was:
            radial.grow(wrap, was, want)

    def drop_bubble(self, lab):
        """Fade a pill out, then take it away.

        It used to be removed outright — the revealer that faded it in
        was never asked to fade it out — so a cancelled utterance
        vanished mid-gesture.
        """
        if lab is None:
            return
        rev = lab._rev

        def gone():
            if rev.get_parent() is self.msgs:
                self.msgs.remove(rev)
            self._refade()

        radial.fade_out_then(rev, gone)

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
            want = 1.0 if age < 2 else max(0.35, 1.0 - 0.16 * age)
            # Every pill's opacity used to be rewritten in one frame, so
            # the whole history stepped back at once each time a line
            # landed. Only what actually changed moves, and it ramps.
            if abs(rev.get_opacity() - want) > 0.01:
                radial.fade(rev, want, 0.25)
        # The column just changed height, so the ring has a different
        # amount of circle to work with than it did a moment ago.
        GLib.idle_add(self._reflow)

    # --- turn flow (UI side; work happens on the asyncio thread) -------
    def start_turn(self):
        if self.phase != "idle" or self._closing:
            return
        self.phase = "listening"
        self._discard = False
        # No empty pill while waiting: the green hub says "listening";
        # the pill appears with the first words.
        self.live_label = None
        self.set_status("warming up…" if not self._warmed else
                        "listening — just talk")
        asyncio.run_coroutine_threadsafe(self._turn(), self.aio)

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

    # --- the card's ring ----------------------------------------------
    def _chrome_items(self):
        """What the card's own ring offers.

        Two groups, and the layout leaves more room between them than
        inside them: getting out of the card, then acting on it.
        """
        return [
            Bubble("go-previous-symbolic", "Back to the menu", self.on_back,
                   key="back", group="leave"),
            Bubble("window-close-symbolic", "Close  [Esc]", self.close,
                   css=("danger",), key="close", group="leave"),
            Bubble("emblem-system-symbolic", "Chat settings",
                   self.open_settings, key="settings", group="act"),
            Bubble("starred-symbolic", "What to do with the answer",
                   self.open_suggest, key="suggest", group="act",
                   shown=False),
        ]

    def _in_the_way(self):
        """What the ring lays itself out around.

        Everything above it: the conversation, the status line, the
        entry. The arc is whatever they leave, so a card with ten
        messages gives the ring a different sweep than a card with one
        and neither of them is a number anybody wrote down.
        """
        return (self.scroll, self.status, self.entry)

    def _hits(self):
        """Where the overlay takes clicks: the card, plus the ring's
        bubbles one at a time rather than the square they are drawn in."""
        return (*self._hit_widgets, *self.ring.hit_widgets())

    def _open_ring(self):
        """Bloom, and reach back to whatever opened this card."""
        self.ring.open()
        handoff.arrive(self, self.canvas, self.ring.hub)
        return False

    def _ring_grew(self, reach):
        """Make room for the ring, then let it use the room.

        A level opening used to be drawn straight over the conversation:
        the ring hangs off the card and simply got bigger. Now the gap it
        sits in grows to match, which lifts the column clear, and the arc
        is re-solved against where the column has moved to — so a deep
        level gets more of the circle than a shallow one, because there
        is more of the circle free.
        """
        want = max(RING_H, 2 * reach + 8)
        if abs(self._gap - want) < 1:
            return
        # Not radial.grow: that releases the height back to natural when
        # it lands, and this gap has no natural height to go back to.
        track = motion.Track(self._gap, want, 0.24)
        self._gap = want
        radial.drive(
            self.ring_gap, 0.24,
            lambda t: self.ring_gap.set_size_request(-1, round(track.at(t))),
            lambda: GLib.idle_add(self._reflow))

    def _reflow(self):
        """Re-solve the ring's arc, after the column changed shape."""
        if not self._closing:
            self.ring.relayout()
        return False

    def _ring_items(self, text, picks):
        """Catalogue bubbles for one piece of text, plus copy."""
        items = [Bubble(p["icon"], p["label"], self._ring_action(p, text),
                        key=f"{p['id']}:{p.get('arg', '')}") for p in picks]
        items.append(Bubble("edit-copy-symbolic", "Copy", self._copy(text),
                            key="copy"))
        return items

    def _ring_action(self, pick, text):
        def run():
            self.ring.go_to(0)
            self.run_action(pick, text)
        return run

    def _copy(self, text):
        def run():
            self.ring.go_to(0)
            subprocess.run(["wl-copy"], input=text.encode(), check=False)
            self.set_status("copied")
        return run

    def _open_actions(self, text):
        """A level of what to do with *text*, and the model's opinion of
        it when that arrives."""
        if not text or self.ring.depth:
            return
        self._acting_on = text
        self.ring.push(self._ring_items(text, self._picks_for(text)),
                       Hub("starred-symbolic", "What to do with this"))
        threading.Thread(target=self._refresh_ring, args=(text,),
                         daemon=True).start()

    def on_msg_menu(self, _btn, lab):
        """The level for one message: what to do with this line."""
        self._open_actions(lab.get_label().strip())

    def open_suggest(self):
        """The level for the conversation: what to do with the answer."""
        self._open_actions(self.history[-1]["content"] if self.history else "")

    def _picks_for(self, text):
        """What the model last suggested for this text, or the staples
        until it answers: a level must open now, not in two seconds."""
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
        if self.ring.depth and self._acting_on == text:
            self.ring.update(self._ring_items(text, picks))
        return False

    # --- the settings level -------------------------------------------
    # Live switches. attr on settings.llm, the config key it is stored
    # under, icon, label.
    SETTINGS_RING = (
        ("details", "chat_details", "view-list-symbolic",
         "Show the chat's working"),
        ("speak", "speak_answers", "audio-speakers-symbolic",
         "Speak answers aloud"),
        ("recall", "recall", "document-open-recent-symbolic",
         "Recall from the archive"),
    )

    def _settings_items(self):
        return [Bubble(icon, f"{label}: {'on' if on else 'off'}",
                       self._toggle(attr, key, label),
                       css=("on",) if on else (), key=key)
                for attr, key, icon, label in self.SETTINGS_RING
                for on in (bool(getattr(settings.llm, attr)),)]

    def _toggle(self, attr, key, label):
        def run():
            on = not bool(getattr(settings.llm, attr))
            write_config({key: on})   # the next chat opens the same way
            self.set_status(f"{label.lower()}: {'on' if on else 'off'}")
            # Same keys, so the ring repaints one bubble green instead of
            # spiralling every bubble out and back to say so.
            self.ring.update(self._settings_items())
        return run

    def open_settings(self):
        self.ring.push(self._settings_items(),
                       Hub("emblem-system-symbolic", "Chat settings"))

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

    def on_back(self):
        handoff.leave(self, self.ring, [str(REPO / "bin" / "dictate-menu")])

    def on_mic(self):
        """Stop listening and send, or start listening again.

        The card listens on its own, so this is a commit far more often
        than it is a start; without the status line it looked like a
        button that did nothing, because ending an utterance early looks
        exactly like the VAD ending it for you."""
        if self.phase == "listening":
            self.set_status("sending…")
            self._commit_now()
        elif self.phase == "idle":
            self.start_turn()

    def _entry_key(self, _c, keyval, _code, _state):
        """Let the ring have a key the empty field has no use for.

        The card focuses its entry as it opens, which is what makes it
        typeable; without this the numbered bubbles would be unreachable
        for as long as the card was up. An empty field loses nothing to
        a digit. Once there is text, a digit is text.
        """
        if self.entry.get_text():
            return False
        return self.ring.handle_key(keyval)

    def on_key(self, _c, keyval, _code, _state):
        # The ring gets the keys first: its bubbles are numbered and its
        # levels answer to Escape, both of which were lies while Escape
        # closed the whole conversation instead. The controller is on
        # the bubble phase, so a focused entry still eats digits before
        # any of this sees them.
        if self.ring.handle_key(keyval):
            return True
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
        self.drop_bubble(self.live_label)
        self.live_label = None
        self.set_status(msg + " — tap the mic to retry", error=True)

    def _got_text(self, text, pcm):
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
        # Archive first: a spoken question is worth keeping even when the
        # answer never arrives, and llm.chat is the part that fails.
        record = None
        if settings.storage.enabled and pcm:
            with contextlib.suppress(OSError, ValueError):
                record = save_recording(
                    pcm, text,
                    storage_base=Path(settings.storage.base).expanduser(),
                    whisper_model=settings.whisper.model,
                    meta={"mode": "ask"})
        # What the model was given and what it did with it. Collected
        # whether or not the card is showing it: the trace is the only
        # record of why an answer came out the way it did, and deciding
        # to look afterwards is too late if it was never kept.
        steps: list[dict] = []

        def step(kind, detail):
            steps.append({"kind": kind, "detail": detail})

        context = []
        if settings.llm.recall and settings.storage.enabled:
            with contextlib.suppress(OSError):
                context = await asyncio.to_thread(recall.search, text)
            if not context:
                step("recall", "nothing relevant in the archive")
        try:
            shot, self.shot = self.shot, None   # asked about once
            answer = await asyncio.to_thread(
                llm.chat, text, context, list(self.history), shot, step)
        except OSError as e:
            GLib.idle_add(self._answer_failed, str(e))
            return
        self.history += [{"role": "user", "content": text},
                         {"role": "assistant", "content": answer}]
        GLib.idle_add(self._show_answer, answer, steps)

        if record is not None and settings.llm.concepts:
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

    def _show_answer(self, answer, steps=None):
        self.log.turn("assistant", answer, trace=steps or None)
        subprocess.run(["wl-copy"], input=answer.encode(), check=False)
        if self.think_label is not None:
            self._grow_into(self.think_label, answer)
            self.think_label = None
        if steps and settings.llm.details:
            self.trace_bubble(steps)
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
            self.ring.set_item_shown("suggest", True)
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
