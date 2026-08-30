#!/usr/bin/python3
"""First-run setup wizard for dictatr: one card, one step at a time.

Three steps, each a probe plus a few choices: the inference engine, the
hotkeys, and a real dictation that also asks for the typing permission
it needs. Short, skippable, re-runnable (`dictate setup`), so the
packages never have to print shell instructions after install.

What each step says and does is in ui/setup_steps.py; this file is the
window that renders it. The seam is nine methods wide -- set_body,
set_status, set_extra, set_items, set_progress, busy, advance,
mark_complete, close -- and it is what keeps the wizard editable:
adding a step is writing one class over there, and how it looks is not
that class's business.

A card on a transparent layer-shell overlay: title and step counter,
the prose, a status line, the choices as a list of labelled rows, and
Back and Close. One column, one alignment, no history. Choices carry
their words because the ring this began as put every action behind an
unlabelled icon and a hover; they are a plain list because the
conversation it became grew a transcript nobody could act on. The input
region is clipped to the card, so the desktop underneath stays
clickable.

Nothing blocks the GTK loop: every probe, download and portal dance runs
on a worker thread and reports back through GLib.idle_add.

DICTATR_SETUP_STEP=N opens straight on one step, for capture and for
eyeballing a step without walking the whole wizard.
"""

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from dictatr.settings import settings, write_config  # noqa: E402

sys.path.insert(0, str(REPO / "ui"))
import handoff  # noqa: E402
import motion  # noqa: E402
import radial  # noqa: E402
from radial import BLUE, GREEN, INK, RED, Bubble  # noqa: E402
from setup_steps import STEPS, Step  # noqa: E402

WIDTH = 420
CARD_H = 420    # the card's own height, for placing it at the pointer

# One card per step. The wizard is not a conversation -- there is
# nothing to scroll back to and nothing to say -- so it stopped being
# shaped like one: a title, a paragraph, the choices, and the two ways
# out. Everything left-aligned in one column, because an eye that has to
# travel left, right and centre on every step is doing work the layout
# should have done.
SETUP_CSS = f"""
window {{ background: transparent; }}
.setup .card {{
  background: alpha(#1c1d22, 0.96);
  border: 1px solid alpha(#ffffff, 0.10);
  border-radius: 18px;
  padding: 20px 22px;
}}
.setup .title {{ font-size: 16px; font-weight: 700; color: {INK}; }}
.setup .counter {{ font-size: 12px; color: alpha({INK}, 0.42); }}
.setup .body {{ color: alpha({INK}, 0.80); }}
.setup .status {{ font-size: 12px; color: alpha({INK}, 0.55); }}
.setup .status.good {{ color: {GREEN}; }}
.setup .status.bad {{ color: {RED}; }}
/* A choice is a labelled row, full width, so the list reads as a list
   and the words are the affordance rather than an icon on an orbit. */
.choice {{
  border-radius: 10px;
  padding: 9px 12px;
  background: alpha(#ffffff, 0.05);
  border: 1px solid alpha(#ffffff, 0.10);
  color: {INK};
  transition: border-color 120ms ease, background 120ms ease;
}}
.choice:hover {{
  border-color: alpha({BLUE}, 0.65);
  background: alpha({BLUE}, 0.14);
}}
.choice image {{ color: alpha({INK}, 0.7); }}
.choice.primary {{ border-color: alpha({BLUE}, 0.5); }}
.choice.primary image {{ color: {BLUE}; }}
/* The number that picks this from the keyboard: present enough to be
   learned, quiet enough not to compete with the words. */
.choice-key {{
  color: alpha({INK}, 0.30); font-size: 11px;
  padding-left: 6px;
}}
/* The two ways out: words, not orbiting icons, and quiet until wanted. */
.nav {{
  background: none; border: none; box-shadow: none;
  padding: 2px 6px; min-height: 0;
  color: alpha({INK}, 0.5); font-size: 13px;
}}
.nav:hover {{ color: {INK}; background: alpha(#ffffff, 0.07); }}
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




# --- the window --------------------------------------------------------


class Wizard(Gtk.ApplicationWindow):
    """The surface: one card, one step at a time.

    A title, the prose, a status line, the choices, and the two ways
    out. It has been three things now — a ring of unlabelled satellites
    where every choice was a hover away from being readable, then a
    conversation with a transcript of pills. The transcript was the
    mistake the ring was not: a three-step wizard has no history worth
    keeping, so it was chrome that grew under the sentence you were
    still reading, in a fourth alignment, while a step badge, a status
    pill and a hub ring each claimed a region of their own.

    Everything is in the card and left-aligned. The overlay's input
    region is clipped to it, so the desktop underneath stays clickable.
    """

    def __init__(self, app):
        super().__init__(application=app, decorated=False,
                         title="Set up dictatr", default_width=WIDTH)
        radial.apply_css(SETUP_CSS)
        self.add_css_class("setup")

        self.steps = [step(self) for step in STEPS]
        self.index = 0
        self.completed = False
        self._closing = False

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")
        card.set_size_request(WIDTH, -1)

        # Header: what this step is, and how much is left.
        self.title_label = Gtk.Label(xalign=0.0, hexpand=True)
        self.title_label.add_css_class("title")
        self.step_label = Gtk.Label(xalign=1.0)
        self.step_label.add_css_class("counter")
        head = Gtk.Box()
        head.append(self.title_label)
        head.append(self.step_label)
        card.append(head)

        self.body = Gtk.Label(xalign=0.0, wrap=True)
        self.body.set_max_width_chars(46)
        self.body.add_css_class("body")
        card.append(self.body)

        # The status is a line of this card, not a pill of its own
        # floating under it: it says something about the step you are
        # reading, so it belongs where you are reading.
        self.status = Gtk.Label(xalign=0.0, wrap=True)
        self.status.set_max_width_chars(46)
        self.status.add_css_class("status")
        self.status.set_visible(False)
        card.append(self.status)

        self.extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.append(self.extra)

        self.choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=6)
        card.append(self.choices)

        self.bar = Gtk.ProgressBar(visible=False)
        card.append(self.bar)
        # Back and Close as two quiet buttons in the card's own footer.
        # They were a ring orbiting a badge, which is chrome the menu
        # and the chat need (they are rings) and this is not: a wizard
        # page has exactly two ways out and they read better as words.
        self.back_btn = Gtk.Button(label="\u2039  Back")
        self.back_btn.add_css_class("nav")
        self.back_btn.set_focusable(False)
        self.back_btn.connect("clicked", lambda *_: self.back())
        close_btn = Gtk.Button(label="Close")
        close_btn.add_css_class("nav")
        close_btn.set_focusable(False)
        close_btn.set_tooltip_text("Close  [Esc]")
        close_btn.connect("clicked", lambda *_: self.close())
        foot = Gtk.Box()
        self.back_btn.set_hexpand(True)
        self.back_btn.set_halign(Gtk.Align.START)
        close_btn.set_halign(Gtk.Align.END)
        foot.append(self.back_btn)
        foot.append(close_btn)
        card.append(foot)

        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stack.append(card)
        self.card = card

        self.column = stack
        self._hit = (card,)

        # The overlay is the kit's, not a second copy of it: this window
        # had its own transcription of layer-shell setup and input-region
        # polling, and the two had already drifted apart.
        # The card arrives under the pointer, centred on it: with one
        # region there is no hub to line up with any more.
        self.ov = radial.Overlay(self, stack, (WIDTH, CARD_H),
                                 hit_widgets=self._hits, clamp=12,
                                 on_place=self._enter_from,
                                 anchor=(0.5, 0.5))
        # Invisible until it is placed. Without this the whole column
        # painted, fully opaque, in the screen's top-left corner and
        # then teleported to the pointer — for up to two seconds, if the
        # compositor was slow to say where the pointer was.
        stack.set_opacity(0.0)
        self.overlay = self.ov.start()
        if self.overlay:
            self.ov.enable_drag(self._draggable_at)
        else:
            stack.set_opacity(1.0)
            outer = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            outer.append(stack)
            self.set_child(outer)
            GLib.timeout_add(120, self._arrive)

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

    def _hits(self):
        """Where the overlay takes clicks: the card, and nothing else.
        Everything the wizard offers is inside it now."""
        return self._hit

    def _enter_from(self, cx, cy):
        """Rise into place, then bloom — the chat card's arrival, on the
        wizard. It used to have no entrance at all."""
        arrival = motion.Timeline(
            alpha=motion.Track(0.0, 1.0, 0.22),
            lift=motion.Track(26.0, 0.0, 0.22),
            bloom=motion.Track(0.0, 1.0, 0.001, delay=0.12))
        bloomed = [False]

        def apply(v):
            self.column.set_opacity(v["alpha"])
            self.ov.move(cx, cy + v["lift"])
            if v["bloom"] > 0.5 and not bloomed[0]:
                bloomed[0] = True
                self._arrive()

        radial.play(self, arrival, apply)

    def _arrive(self):
        """Draw the tether back to whatever opened us, to the card."""
        handoff.arrive(self, self.ov.canvas, self.card)
        return False

    def _draggable_at(self, x, y):
        """Anywhere on the column that is not a control moves it."""
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        on_card = False
        while target is not None:
            if isinstance(target, (Gtk.Button, Gtk.Entry)):
                return False
            if target is self.column or target is self.card:
                on_card = True
            target = target.get_parent()
        return on_card

    # --- what the steps call ---------------------------------------------
    def set_body(self, text):
        """The step's prose. One paragraph, in the card."""
        self.body.set_label(text)

    def set_status(self, text, tone=""):
        """Crossfade the status line; the pill itself fades in and out.

        It used to appear and disappear outright, changing the column's
        height in one frame in the middle of a probe."""
        self._status = (text, tone)

        def apply():
            label, colour = self._status
            for t in ("good", "bad"):
                self.status.remove_css_class(t)
            if colour:
                self.status.add_css_class(colour)
            self.status.set_label(label)
            self.status.set_visible(bool(label))

        if self.status.get_label() == text and bool(text):
            apply()
        elif not text:
            radial.fade_out_then(self.status, apply)
        else:
            was_empty = not self.status.get_visible()
            apply()
            if was_empty:
                self.status.set_opacity(0.0)
                radial.fade(self.status, 1.0, 0.20)
            else:
                self.status.set_opacity(1.0)

    def set_progress(self, fraction, text=None):
        """A real bar with a real number under it. None clears both."""
        if fraction is None:
            radial.fade_out_then(self.bar, self._hide_bar)
            return
        if not self.bar.get_visible():
            self.bar.set_opacity(0.0)
            self.bar.set_visible(True)
            radial.fade(self.bar, 1.0, 0.20)
        self.bar.set_visible(True)
        self.bar.set_fraction(max(0.0, min(1.0, float(fraction))))
        if text:
            self.set_status(text)

    def _hide_bar(self):
        self.bar.set_visible(False)
        self.bar.set_fraction(0.0)
        self.bar.set_opacity(1.0)

    def busy(self, on):
        """While a worker runs with no percentage to show, pulse."""
        if on:
            if not self.bar.get_visible():
                self.bar.set_opacity(0.0)
                self.bar.set_visible(True)
                radial.fade(self.bar, 1.0, 0.20)
            if self._pulse is None:
                self._pulse = GLib.timeout_add(90, self._do_pulse)
        elif self._pulse is not None:
            GLib.source_remove(self._pulse)
            self._pulse = None
            if self.bar.get_fraction() <= 0:
                radial.fade_out_then(self.bar, self._hide_bar)

    _pulse = None
    # The status the pill is on its way to showing, so two in quick
    # succession land on the later one rather than on whichever fade
    # happened to survive.
    _status = ("", "")

    def _do_pulse(self):
        self.bar.pulse()
        return True

    def set_extra(self, widget=None):
        while child := self.extra.get_first_child():
            self.extra.remove(child)
        if widget is not None:
            self.extra.append(widget)

    def _choice_button(self, i, bubble):
        btn = Gtk.Button()
        btn.add_css_class("choice")
        if i == 0:
            btn.add_css_class("primary")
        row = Gtk.Box(spacing=9)
        row.append(Gtk.Image(icon_name=bubble.icon))
        row.append(Gtk.Label(label=bubble.tooltip, hexpand=True, xalign=0.0))
        key = Gtk.Label(label=str(i + 1))
        key.add_css_class("choice-key")
        row.append(key)
        btn.set_child(row)
        # A focused button fires on space or Return, so a stray keypress
        # used to activate whatever held focus.
        btn.set_focusable(False)
        btn.connect("clicked", self._chose, bubble)
        return btn

    def set_items(self, bubbles):
        """Render the step's choices as labelled pills.

        Steps hand over radial Bubbles, whose tooltip was always the
        readable name of the action — here that name is the button, and
        the icon is only decoration beside it.

        They leave and arrive one after another rather than being torn
        down and rebuilt in a single frame. The choices are the wizard's
        primary interaction and they used to be the one part of it with
        no transition at all: picking one faded a pill into the
        transcript while the list it came from blinked out of existence.
        """
        specs = list(bubbles)
        going = self._choice_buttons()

        def arrive():
            _, per = motion.stagger(len(specs), 0.30, 0.05)
            for i, spec in enumerate(specs):
                btn = self._choice_button(i, spec)
                btn.set_opacity(0.0)
                self.choices.append(btn)
                radial.fade(btn, 1.0, 0.22, delay=i * per)

        if not going:
            arrive()
            return

        left = [len(going)]

        def gone(btn):
            if btn.get_parent() is self.choices:
                self.choices.remove(btn)
            left[0] -= 1
            if left[0] == 0:
                arrive()

        for i, btn in enumerate(going):
            radial.fade_out_then(btn, lambda b=btn: gone(b),
                                 duration=0.14, delay=i * 0.03)

    def _chose(self, _btn, bubble):
        """Picking clears the list and runs the action. What you chose
        used to be echoed back as a pill above; in a three-step wizard
        that was a transcript of things you could not act on, growing
        under prose you were still reading."""
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
        self.index = index
        self.set_extra(None)
        self.set_items([])
        self.set_status("")
        self.set_progress(None)
        self.busy(False)
        self.title_label.set_label(step.title)
        self.step_label.set_label(f"{index + 1} of {len(self.steps)}")
        # Nothing to go back to on the first step, and Close is right
        # there: a disabled Back would only be a thing to try.
        self.back_btn.set_visible(index > 0)
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
        i = radial.digit_index(keyval, len(buttons))
        if i is not None:
            buttons[i].emit("clicked")
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
