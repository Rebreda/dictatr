"""Which screenshot tool gets picked.

The portal always works and is the floor; a desktop editor that can
crop, blur and annotate in the same drag is the point of taking the
screenshot at all, so it wins wherever it is installed.
"""

import subprocess
from pathlib import Path

import pytest

from dictatr import shot


@pytest.fixture
def installed(monkeypatch):
    """Control what is on PATH."""
    def have(names):
        monkeypatch.setattr(shot.shutil, "which",
                            lambda n: f"/usr/bin/{n}" if n in names else None)
    return have


DEST = Path("/run/user/1000/dictatr/shot-1.png")


def test_kde_gets_spectacle(installed):
    installed({"spectacle"})
    assert shot.tool_argv(DEST, "auto") == [
        "spectacle", "-b", "-n", "-r", "-o", str(DEST)]


def test_wlroots_gets_an_annotating_pipeline(installed):
    installed({"grim", "slurp", "satty"})
    argv = shot.tool_argv(DEST, "auto")
    assert argv[0] == "sh" and argv[-1] == str(DEST)
    assert "satty" in argv[2]


def test_swappy_is_taken_only_when_satty_is_missing(installed):
    installed({"grim", "slurp", "swappy"})
    assert "swappy" in shot.tool_argv(DEST, "auto")[2]


def test_half_a_pipeline_is_not_a_tool(installed):
    """grim without slurp cannot select a region, so it is not an
    editor and the portal is better than half of one."""
    installed({"grim"})
    assert shot.tool_argv(DEST, "auto") is None


def test_a_bare_desktop_falls_through_to_the_portal(installed):
    installed(set())
    assert shot.tool_argv(DEST, "auto") is None


def test_portal_is_forced_even_where_a_tool_exists(installed):
    installed({"spectacle"})
    assert shot.tool_argv(DEST, "portal") is None


def test_a_configured_command_wins(installed):
    installed({"spectacle"})
    assert shot.tool_argv(DEST, "flameshot gui -p {path}") == [
        "flameshot", "gui", "-p", str(DEST)]


def test_a_path_with_a_space_stays_one_argument(installed):
    installed({"grim", "slurp", "satty"})
    dest = Path("/tmp/my shots/a.png")
    assert shot.tool_argv(dest, "auto")[-1] == str(dest)


def test_a_cancelled_tool_writes_nothing_and_means_nothing(
        tmp_path, monkeypatch, installed):
    """Every editor here exits cleanly when you press Esc, so the file
    is the only honest signal that a screenshot happened."""
    installed({"spectacle"})
    monkeypatch.setattr(shot.subprocess, "run", lambda *a, **k: None)
    assert shot.capture(tmp_path / "shot.png") is None


def test_a_finished_tool_is_taken_at_its_word(tmp_path, monkeypatch,
                                              installed):
    installed({"spectacle"})
    dest = tmp_path / "shot.png"
    monkeypatch.setattr(shot.subprocess, "run",
                        lambda *a, **k: dest.write_bytes(b"PNG..."))
    assert shot.capture(dest) == dest


# --- the tiers ---------------------------------------------------------

@pytest.fixture
def tiers(tmp_path, monkeypatch, installed):
    """Watch which tier a capture actually took."""
    took = []

    def portal(interactive, timeout):
        took.append(f"portal({'pick' if interactive else 'full'})")
        raw = tmp_path / "from-portal.png"
        raw.write_bytes(b"PNG")
        return raw

    def annotate(argv, extra=None):
        took.append("annotate")
        return 0

    monkeypatch.setattr(shot, "portal_capture", portal)
    monkeypatch.setattr(shot, "annotator", lambda: ["py", "annotate.py"])

    def run(argv, **kw):
        if "annotate.py" in argv:
            took.append("annotate")
            Path(argv[argv.index("--out") + 1]).write_bytes(b"PNG")
        else:
            took.append(argv[0])
            Path(argv[-1]).write_bytes(b"PNG")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(shot.subprocess, "run", run)
    return took


def test_a_desktop_editor_is_preferred_over_ours(tiers, tmp_path, installed,
                                                 monkeypatch):
    installed({"spectacle"})
    monkeypatch.setattr(shot.settings.shot, "tool", "auto")
    assert shot.capture(tmp_path / "s.png") is not None
    assert tiers == ["spectacle"]


def test_without_one_the_portal_feeds_our_editor(tiers, tmp_path, installed,
                                                 monkeypatch):
    """A bare desktop still crops and annotates: the portal takes the
    whole screen and the selecting happens in our overlay."""
    installed(set())
    monkeypatch.setattr(shot.settings.shot, "tool", "auto")
    assert shot.capture(tmp_path / "s.png") is not None
    assert tiers == ["portal(full)", "annotate"]


def test_dictatr_is_used_even_where_spectacle_exists(tiers, tmp_path,
                                                     installed, monkeypatch):
    installed({"spectacle"})
    monkeypatch.setattr(shot.settings.shot, "tool", "dictatr")
    shot.capture(tmp_path / "s.png")
    assert tiers == ["portal(full)", "annotate"]


def test_a_portal_that_refuses_a_silent_grab_gets_the_picker(
        tiers, tmp_path, installed, monkeypatch):
    installed(set())
    monkeypatch.setattr(shot.settings.shot, "tool", "auto")
    calls = []

    def picky(interactive, timeout):
        calls.append(interactive)
        if not interactive:
            return None            # this desktop insists on a picker
        raw = tmp_path / "raw.png"
        raw.write_bytes(b"PNG")
        return raw

    monkeypatch.setattr(shot, "portal_capture", picky)
    assert shot.capture(tmp_path / "s.png") is not None
    assert calls == [False, True]


def test_no_gtk_means_the_bare_capture(tiers, tmp_path, installed,
                                       monkeypatch):
    installed(set())
    monkeypatch.setattr(shot.settings.shot, "tool", "auto")
    monkeypatch.setattr(shot, "annotator", lambda: None)
    assert shot.capture(tmp_path / "s.png") is not None
    assert tiers == ["portal(pick)"]


def test_cancelling_the_editor_leaves_no_file(tiers, tmp_path, installed,
                                              monkeypatch):
    """The raw grab must not survive as if it were the finished shot."""
    installed(set())
    monkeypatch.setattr(shot.settings.shot, "tool", "auto")
    monkeypatch.setattr(shot.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1))
    dest = tmp_path / "s.png"
    assert shot.capture(dest) is None
    assert not dest.exists()
    assert not dest.with_suffix(".raw.png").exists()
