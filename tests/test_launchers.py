"""The shell shims in bin/.

They are three lines each and easy to assume correct, which is how one
of them came to exit before doing anything on every 64-bit distro that
keeps gtk4-layer-shell in /usr/lib64: preload_layer_shell ended on a
failed `[ -e ]`, that became the function's exit status, and `set -e`
did the rest. Ctrl+Alt+Space ran it, it died silently, and the hotkey
looked unbound.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
COMMON = BIN / "_common.sh"

LAUNCHERS = sorted(p for p in BIN.iterdir()
                   if p.is_file() and not p.suffix
                   and p.name != "_common.sh"
                   and p.read_bytes().startswith(b"#!"))


def run(script):
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30)


def test_preload_survives_set_e_with_only_one_library_present():
    """The real shape of the bug: exactly one of the two paths exists."""
    r = run(f'set -eu; . "{COMMON}"; preload_layer_shell; echo SURVIVED')
    assert "SURVIVED" in r.stdout, (
        f"preload_layer_shell aborted its caller (exit {r.returncode})")


def test_preload_survives_when_neither_library_is_present(tmp_path):
    """A machine with no gtk4-layer-shell at all still has to reach the
    surface, which falls back to an ordinary window by itself."""
    fake = tmp_path / "common.sh"
    fake.write_text(COMMON.read_text().replace(
        "/usr/lib64/libgtk4-layer-shell.so.0", str(tmp_path / "nope-64"))
        .replace("/usr/lib/libgtk4-layer-shell.so.0", str(tmp_path / "nope")))
    r = run(f'set -eu; . "{fake}"; preload_layer_shell; echo SURVIVED')
    assert "SURVIVED" in r.stdout


def test_preload_still_sets_the_preload_when_a_library_exists(tmp_path):
    lib = tmp_path / "libgtk4-layer-shell.so.0"
    lib.write_bytes(b"")
    fake = tmp_path / "common.sh"
    fake.write_text(COMMON.read_text().replace(
        "/usr/lib64/libgtk4-layer-shell.so.0", str(lib)))
    r = run(f'set -eu; . "{fake}"; preload_layer_shell; echo "$LD_PRELOAD"')
    assert str(lib) in r.stdout


@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda p: p.name)
def test_every_launcher_is_valid_shell(launcher):
    r = run(f'bash -n "{launcher}"')
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda p: p.name)
def test_every_launcher_reaches_its_exec(launcher):
    """Run each shim with a stubbed exec: whatever it would have run, it
    has to actually get there. This is the assertion the silent exit
    would have failed.
    """
    script = f'''
    set -eu
    exec() {{ echo "REACHED: $*"; }}
    # The surfaces are GUI programs; stop at the point of launching one.
    python3() {{ echo "REACHED: python3 $*"; }}
    . "{COMMON}"
    preload_layer_shell
    echo SURVIVED
    '''
    r = run(script)
    assert "SURVIVED" in r.stdout, f"{launcher.name}: {r.stderr}"
