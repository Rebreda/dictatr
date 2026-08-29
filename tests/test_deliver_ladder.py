"""Delivery ladder ordering and the portal helper's pure logic.

portal_typed keeps its gi imports lazy exactly so this suite (venv, no
PyGObject) can exercise the token file handling and keysym mapping.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
import portal_typed  # noqa: E402

from dictatr import deliver  # noqa: E402


# --- keysym mapping ----------------------------------------------------

def test_keysym_ascii_printables_map_directly():
    for cp in range(0x20, 0x7f):
        assert portal_typed.keysym_for(chr(cp)) == cp


def test_keysym_latin1_maps_directly():
    assert portal_typed.keysym_for("é") == 0xE9
    assert portal_typed.keysym_for("\xa0") == 0xA0


def test_keysym_specials_and_controls():
    assert portal_typed.keysym_for("\n") == 0xFF0D
    assert portal_typed.keysym_for("\r") == 0xFF0D
    assert portal_typed.keysym_for("\t") == 0xFF09
    assert portal_typed.keysym_for("\x07") is None
    assert portal_typed.keysym_for("\x7f") is None


def test_keysym_unicode_rule():
    # Beyond Latin-1: keysym = 0x01000000 + codepoint
    assert portal_typed.keysym_for("€") == 0x01000000 + 0x20AC
    assert portal_typed.keysym_for("日") == 0x01000000 + ord("日")


# --- token file --------------------------------------------------------

def test_token_roundtrip_creates_dirs_and_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert portal_typed.load_token() is None
    portal_typed.save_token("tok123")
    p = portal_typed.token_path()
    assert p == tmp_path / "state" / "dictatr" / "portal-typing-token"
    assert p.stat().st_mode & 0o777 == 0o600
    assert portal_typed.load_token() == "tok123"
    portal_typed.drop_token()
    assert portal_typed.load_token() is None
    portal_typed.drop_token()   # idempotent


def test_token_paths_agree_between_helper_and_deliver(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert deliver._portal_token() == portal_typed.token_path()


# --- ladder ordering ---------------------------------------------------

@pytest.fixture
def ladder(tmp_path, monkeypatch):
    """Fake every external: record which tools run, control outcomes."""
    calls = []
    rc = {"portal": 0, "command": 0}
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("DICTATE_NO_PORTAL", raising=False)
    monkeypatch.setattr(deliver.runstate, "mark_done", lambda: None)
    monkeypatch.setattr(deliver, "notify", lambda *a, **k: None)
    monkeypatch.setattr(deliver.shutil, "which", lambda name: f"/bin/{name}")
    # Typing waits for the hotkey chord to be released; no chord here.
    monkeypatch.setattr(deliver.runstate, "chord_held", lambda *a: False)
    # The portal tier has a config escape hatch; the ladder tests are
    # about ordering, so keep it on and let each case opt out.
    from dictatr import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings.typing, "portal", True)

    def fake_run(cmd, **kw):
        if str(deliver._PORTAL_HELPER) in cmd:
            tool = "portal"
        elif cmd[0] == "type-shim":
            tool = "command"
        else:
            tool = Path(cmd[0]).name
        calls.append(tool)
        return subprocess.CompletedProcess(cmd, rc.get(tool, 0))

    monkeypatch.setattr(deliver.subprocess, "run", fake_run)

    def with_token():
        deliver._portal_token().parent.mkdir(parents=True, exist_ok=True)
        deliver._portal_token().write_text("tok")

    return calls, rc, with_token


def test_portal_types_by_default(ladder):
    calls, _rc, with_token = ladder
    with_token()
    assert deliver.deliver("hi") == "typed"
    assert calls == ["portal"]


def test_type_cmd_seam_wins_when_set(ladder, monkeypatch):
    """The demo stage sets DICTATE_TYPE_CMD; nothing else does."""
    calls, _rc, with_token = ladder
    with_token()
    monkeypatch.setenv("DICTATE_TYPE_CMD", "type-shim")
    assert deliver.deliver("hi") == "typed"
    assert calls == ["command"]


def test_type_cmd_failure_falls_to_portal(ladder, monkeypatch):
    calls, rc, with_token = ladder
    with_token()
    monkeypatch.setenv("DICTATE_TYPE_CMD", "type-shim")
    rc["command"] = 1
    assert deliver.deliver("hi") == "typed"
    assert calls == ["command", "portal"]


def test_no_token_skips_portal(ladder):
    calls, _rc, _ = ladder
    assert deliver.deliver("hi") == "clipboard"
    assert calls == ["wl-copy"]


def test_config_can_disable_portal(ladder, monkeypatch):
    from dictatr import settings as settings_mod
    calls, rc, with_token = ladder
    with_token()
    monkeypatch.setattr(settings_mod.settings.typing, "portal", False)
    assert deliver.deliver("hi") == "clipboard"
    assert calls == ["wl-copy"]


def test_env_disables_portal(ladder, monkeypatch):
    calls, rc, with_token = ladder
    with_token()
    monkeypatch.setenv("DICTATE_NO_PORTAL", "1")
    assert deliver.deliver("hi") == "clipboard"
    assert calls == ["wl-copy"]


def test_typing_waits_for_the_chord_to_be_released(ladder, monkeypatch):
    """Injecting while Ctrl+Alt are still physically down is what leaves
    the desktop acting as though Ctrl is stuck."""
    calls, rc, with_token = ladder
    with_token()
    held = {"n": 3}

    def chord_held(*_a):
        held["n"] -= 1
        return held["n"] > 0

    monkeypatch.setattr(deliver.runstate, "chord_held", chord_held)
    monkeypatch.setattr(deliver.time, "sleep", lambda _s: None)
    assert deliver.deliver("hi") == "typed"
    assert held["n"] == 0            # it waited until the chord cleared
    assert calls == ["portal"]


def test_chord_wait_is_bounded(ladder, monkeypatch):
    """A chord nobody releases must not hang delivery forever."""
    calls, rc, with_token = ladder
    with_token()
    monkeypatch.setattr(deliver.runstate, "chord_held", lambda *_a: True)
    clock = {"t": 0.0}
    monkeypatch.setattr(deliver.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(deliver.time, "sleep",
                        lambda s: clock.__setitem__("t", clock["t"] + s))
    assert deliver.deliver("hi") == "typed"
    assert calls == ["portal"]


def test_all_typing_fails_lands_on_clipboard(ladder):
    calls, rc, with_token = ladder
    with_token()
    rc["portal"] = 1
    assert deliver.deliver("hi") == "clipboard"
    assert calls == ["portal", "wl-copy"]


def test_prefer_typing_false_goes_straight_to_clipboard(ladder):
    calls, _rc, with_token = ladder
    with_token()
    assert deliver.deliver("hi", prefer_typing=False) == "clipboard"
    assert calls == ["wl-copy"]


def test_needs_shift_covers_case_and_symbols():
    # The compositor presses Shift a keystroke late and never lifts it,
    # so the helper decides for itself which characters need it.
    for ch in "NX!?():\"":
        assert portal_typed.needs_shift(ch), ch
    for ch in "nx1,.;'-= ":
        assert not portal_typed.needs_shift(ch), ch


def test_edit_to_appends_and_rewrites_revisions():
    from dictatr.livetype import edit_to
    # the common case: the transcript only grew
    assert edit_to("Meet me", "Meet me there") == (0, " there")
    # a revised word costs the tail after the change, not the whole line
    assert edit_to("Meet me their", "Meet me there") == (2, "re")
    assert edit_to("abc", "abc") == (0, "")
    assert edit_to("abc", "") == (3, "")
