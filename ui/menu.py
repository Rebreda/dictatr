#!/usr/bin/python3
"""The audio file picker, and the shell's surface in a process of its own.

What used to live here was the menu — a ring of GtkButtons twirling out
of the cursor point — and the settings window, a GtkGrid of dropdowns and
switches behind a Save button. Both are scenes now (ui/scenes.py), drawn
by the canvas in the resident shell, because two visual vocabularies for
one program is one too many and the ring is the one that answers the
pointer where it already is.

What is left is a file dialog, which is the desktop's own surface rather
than ours, and the escape hatch that opens a scene without a resident
shell behind it.

    dictate-menu --standalone              the menu, in this process
    dictate-menu --standalone --settings   ...opened on the settings scene
    dictate-menu --file                    the audio file picker, alone

--standalone stays because some things genuinely need a surface in a
process of their own: tools/enginepreview and the demo stage each hand
one a scratch XDG_CONFIG_HOME, and settings.py reads that at import, so
a resident shell cannot be given one.
"""

import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DICTATE = str(REPO / "bin" / "dictate")
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "ui"))
import shell  # noqa: E402


class Standalone(shell.Shell):
    """The shell's surface, with no shell behind it.

    A resident shell is put away and kept; this one has nowhere to be
    put away to, so dismissing it is the end of the process. Everything
    else — the graph, the canvas, the scenes — is the same object, which
    is the point: an escape hatch that drew its own menu would be a
    second menu to keep in step with the first.
    """

    def hide(self):
        # Nowhere to be put away to: once the ring is back in the hub
        # the window is the last thing holding the process open.
        self.showing = False
        self.destroy()


def choose_audio_file(parent, done):
    """Ask for an audio file and transcribe it; *done* gets the path, or
    None if the dialog was dismissed.

    Module level because two callers want it: `dictate-menu --file`, and
    the shell's More ring, which calls that until it draws a picker of
    its own. One dialog and one filter list, so the two cannot drift.
    """
    dialog = Gtk.FileDialog(title="Transcribe audio file")
    f = Gtk.FileFilter()
    f.set_name("Audio files")
    for m in ("audio/x-wav", "audio/mpeg", "audio/mp4", "audio/ogg",
              "audio/flac", "audio/webm"):
        f.add_mime_type(m)
    dialog.set_default_filter(f)

    def _done(d, res):
        try:
            gfile = d.open_finish(res)
        except GLib.Error:
            done(None)
            return
        subprocess.Popen([DICTATE, "file", gfile.get_path()])
        done(gfile.get_path())

    dialog.open(parent, None, _done)


class MenuApp(Gtk.Application):
    def __init__(self, mode="menu"):
        super().__init__(application_id=f"io.github.rebreda.dictatr.{mode}")
        self.mode = mode

    def do_activate(self):
        # No window of our own: the picker is the whole surface. Hold the
        # application up while the dialog is open, since it answers on a
        # callback and there is nothing else keeping the loop alive.
        if self.mode == "file":
            self.hold()
            choose_audio_file(None, lambda _path: self.release())
            return
        Standalone(self).open(self.mode)


def main():
    flags = {"--file": "file", "--settings": "settings",
             "--suggest": "suggest", "--standalone": "menu"}
    mode = next((m for f, m in flags.items() if f in sys.argv), "menu")
    sys.exit(MenuApp(mode).run([a for a in sys.argv if a not in flags]))


if __name__ == "__main__":
    main()
