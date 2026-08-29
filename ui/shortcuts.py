"""The global shortcuts, defined once.

The tray binds these through the portal and spawns their commands, the
setup wizard shows them, and bin/dictate-hotkeys writes the same set into
kglobalshortcutsrc for desktops without the portal. They used to be
three lists that had to agree.

Each entry: (id, description, preferred trigger, command).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
DICTATE = str(BIN / "dictate")

SHORTCUTS = [
    ("dictate", "Dictate at cursor", "CTRL+ALT+d", [DICTATE, "type"]),
    ("menu", "Open the dictate menu", "CTRL+ALT+space",
     [str(BIN / "dictate-menu")]),
    ("chat", "Ask the AI (voice chat)", "CTRL+ALT+q",
     [str(BIN / "dictate-chat")]),
    ("cancel", "Cancel dictation", "CTRL+ALT+c", [DICTATE, "cancel"]),
    ("listen", "Toggle always-on capture", "CTRL+ALT+a",
     [DICTATE, "listen", "--toggle"]),
]


def pretty(trigger: str) -> str:
    """CTRL+ALT+d -> Ctrl + Alt + D, for showing a person."""
    parts = [p.capitalize() if len(p) > 1 else p.upper()
             for p in trigger.split("+")]
    return " + ".join(parts)


def desktop_name(sid: str) -> str:
    """The .desktop basename for a shortcut, which is also the group
    bin/dictate-hotkeys writes into kglobalshortcutsrc."""
    return "dictate" if sid == "dictate" else f"dictate-{sid}"


def command(cmd: list) -> str:
    """The command as a desktop entry spells it: the launcher's name
    plus its arguments, for the caller to prefix with its bindir."""
    return " ".join([Path(cmd[0]).name, *cmd[1:]])


if __name__ == "__main__":
    # Emitted for the shell that installs things, so the desktop entries,
    # the KDE fallback bindings and the portal registration cannot drift.
    #   --kde      "<id>\t<keys>\t<description>"
    #   --desktop  "<basename>\t<label>\t<command>"
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "--kde"
    for sid, desc, trigger, cmd in SHORTCUTS:
        if mode == "--desktop":
            print(f"{desktop_name(sid)}\t{desc}\t{command(cmd)}")
        else:
            print(f"{sid}\t{pretty(trigger).replace(' ', '')}\t{desc}")
