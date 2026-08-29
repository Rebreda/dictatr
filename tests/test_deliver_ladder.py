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
    rc = {"portal": 0, "ydotool": 0}
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("DICTATE_NO_PORTAL", raising=False)
    monkeypatch.setattr(deliver.runstate, "mark_done", lambda: None)
    monkeypatch.setattr(deliver, "notify", lambda *a, **k: None)
    monkeypatch.setattr(deliver.shutil, "which", lambda name: f"/bin/{name}")
    # The portal tier is opt-in; these tests exercise the ladder, so turn
    # it on and let each case decide whether it gets that far.
    monkeypatch.setattr(deliver, "_portal_enabled", lambda: True)

    def fake_run(cmd, **kw):
        tool = "portal" if str(deliver._PORTAL_HELPER) in cmd else Path(cmd[0]).name
        calls.append(tool)
        return subprocess.CompletedProcess(cmd, rc.get(tool, 0))

    monkeypatch.setattr(deliver.subprocess, "run", fake_run)

    def with_token():
        deliver._portal_token().parent.mkdir(parents=True, exist_ok=True)
        deliver._portal_token().write_text("tok")

    return calls, rc, with_token


def test_ydotool_is_tried_first(ladder):
    """The portal desyncs the compositor's modifier state when it injects
    alongside a held hotkey chord, so it is the fallback, not the default."""
    calls, _rc, with_token = ladder
    with_token()
    assert deliver.deliver("hi") == "typed"
    assert calls == ["ydotool"]


def test_no_ydotool_falls_to_portal(ladder, monkeypatch):
    calls, _rc, with_token = ladder
    with_token()
    monkeypatch.setattr(deliver.shutil, "which",
                        lambda name: None if name == "ydotool" else f"/bin/{name}")
    assert deliver.deliver("hi") == "typed"
    assert calls == ["portal"]


def test_ydotool_failure_falls_to_portal(ladder):
    calls, rc, with_token = ladder
    with_token()
    rc["ydotool"] = 1
    assert deliver.deliver("hi") == "typed"
    assert calls == ["ydotool", "portal"]


def test_no_token_skips_portal(ladder):
    calls, rc, _ = ladder
    rc["ydotool"] = 1
    assert deliver.deliver("hi") == "clipboard"
    assert calls == ["ydotool", "wl-copy"]


def test_portal_is_opt_in(ladder, monkeypatch):
    """Default config: ydotool, then the clipboard. Never the portal."""
    calls, rc, with_token = ladder
    with_token()
    rc["ydotool"] = 1
    monkeypatch.setattr(deliver, "_portal_enabled", lambda: False)
    assert deliver.deliver("hi") == "clipboard"
    assert calls == ["ydotool", "wl-copy"]


def test_portal_opt_in_reads_config(monkeypatch):
    from dictatr import settings as settings_mod
    monkeypatch.delenv("DICTATE_NO_PORTAL", raising=False)
    monkeypatch.setattr(settings_mod.settings.typing, "portal", False)
    assert deliver._portal_enabled() is False
    monkeypatch.setattr(settings_mod.settings.typing, "portal", True)
    assert deliver._portal_enabled() is True


def test_env_overrides_the_opt_in(monkeypatch):
    from dictatr import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings.typing, "portal", True)
    monkeypatch.setenv("DICTATE_NO_PORTAL", "1")
    assert deliver._portal_enabled() is False


def test_all_typing_fails_lands_on_clipboard(ladder):
    calls, rc, with_token = ladder
    with_token()
    rc["portal"] = rc["ydotool"] = 1
    assert deliver.deliver("hi") == "clipboard"
    assert calls == ["ydotool", "portal", "wl-copy"]


def test_prefer_typing_false_goes_straight_to_clipboard(ladder):
    calls, _rc, with_token = ladder
    with_token()
    assert deliver.deliver("hi", prefer_typing=False) == "clipboard"
    assert calls == ["wl-copy"]
