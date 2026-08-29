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


if __name__ == "__main__":
    # bin/dictate-hotkeys reads this: "<id>\t<trigger>\t<description>".
    for sid, desc, trigger, _cmd in SHORTCUTS:
        keys = pretty(trigger).replace(" ", "")
        print(f"{sid}\t{keys}\t{desc}")
