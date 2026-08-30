"""Typing at the cursor while the transcript is still being revised.

The bug these exist for: dictation is bound to Ctrl+Alt+D, and live
typing starts while that chord may still be physically held — at the
beginning, and again on the press that ends the utterance. A letter
injected then is not a letter, it is Ctrl+Alt+<letter>, and on a desktop
full of global shortcuts that opens windows and switches desktops. It
looks like the app has gone haywire; it is the app typing a command.
"""

import pytest

import portal_typed
from dictatr import livetype


class FakeProc:
    """Stands in for the portal helper: records edits, answers 'ok'."""

    def __init__(self):
        self.edits = []
        self.stdin = self
        self.stdout = self

    def write(self, line):
        import json
        self.edits.append(json.loads(line))

    def flush(self):
        pass

    def readline(self):
        return "ok\n"

    def poll(self):
        return None

    def close(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


@pytest.fixture
def typer(monkeypatch):
    proc = FakeProc()
    monkeypatch.setattr(livetype.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(livetype.runstate, "chord_held", lambda **k: False)
    t = livetype.LiveTyper("python3")
    t.started -= livetype.SETTLE_S      # past the opening settle
    return t


# --- the diff ----------------------------------------------------------

def test_only_the_common_prefix_survives():
    assert livetype.edit_to("", "hello") == (0, "hello")
    assert livetype.edit_to("hello", "hello there") == (0, " there")
    # "the" is the common prefix, so six characters go and "re dog"
    # is retyped -- a mid-word revision retypes everything after it.
    assert livetype.edit_to("their dog", "there dog") == (6, "re dog")
    assert livetype.edit_to("hello", "") == (5, "")
    assert livetype.edit_to("same", "same") == (0, "")


# --- the guard ---------------------------------------------------------

def test_nothing_is_typed_while_the_chord_is_down(typer, monkeypatch):
    """The whole bug, as one assertion."""
    monkeypatch.setattr(livetype.runstate, "chord_held", lambda **k: True)
    typer.update("hello")
    assert typer.proc.edits == []
    assert typer.typed == ""


def test_nothing_is_typed_in_the_moment_after_the_hotkey(typer):
    """Where the hotkey came from kglobalshortcutsrc nothing reports the
    chord at all, so the settle window is the only guard there is."""
    import time
    typer.started = time.monotonic()
    assert typer.holding()
    typer.update("hello")
    assert typer.proc.edits == []


def test_a_skipped_revision_costs_nothing(typer, monkeypatch):
    """Skipping is free because this is a reconciliation, not an append:
    the next partial types the difference from what is really on screen."""
    monkeypatch.setattr(livetype.runstate, "chord_held", lambda **k: True)
    typer.update("hello")
    typer.update("hello there")
    assert typer.proc.edits == []
    monkeypatch.setattr(livetype.runstate, "chord_held", lambda **k: False)
    typer.update("hello there world")
    assert typer.proc.edits == [{"back": 0, "text": "hello there world"}]
    assert typer.typed == "hello there world"


def test_typing_resumes_once_the_chord_is_released(typer, monkeypatch):
    held = {"down": True}
    monkeypatch.setattr(livetype.runstate, "chord_held",
                        lambda **k: held["down"])
    typer.update("one")
    assert typer.proc.edits == []
    held["down"] = False
    typer.update("one two")
    assert typer.proc.edits == [{"back": 0, "text": "one two"}]


def test_a_revision_erases_only_what_changed(typer):
    typer.update("their")
    typer.update("there")
    assert typer.proc.edits[-1] == {"back": 2, "text": "re"}


def test_a_failed_helper_stops_trying(typer):
    typer.failed = True
    typer.update("hello")
    assert typer.proc.edits == []


# --- the injector's own defence ----------------------------------------

def test_shift_is_not_treated_as_a_command_modifier():
    """Shift+a is "A", not a command. Releasing it before typing would
    fight type_text, which manages it explicitly."""
    shift = (0xffe1, 0xffe2)
    assert not set(shift) & set(portal_typed.SHORTCUT_KEYSYMS)
    assert set(shift) <= set(portal_typed.MODIFIER_KEYSYMS)


def test_every_command_modifier_is_released_before_typing():
    """Ctrl, Alt, Super and Meta, both sides. These are the ones that
    turn a dictated letter into a global shortcut."""
    assert set(portal_typed.SHORTCUT_KEYSYMS) == {
        0xffe3, 0xffe4,   # Control
        0xffe9, 0xffea,   # Alt
        0xffeb, 0xffec,   # Super
        0xffe7, 0xffe8,   # Meta
    }
    assert set(portal_typed.SHORTCUT_KEYSYMS) <= set(
        portal_typed.MODIFIER_KEYSYMS)
