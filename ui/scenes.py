#!/usr/bin/python3
"""What each surface is, as nodes.

A scene used to be a program. Here it is a handful of graph.Node records
and the actions their leaves fire — the menu is a list of things you can
do, and that is genuinely all it was underneath the window it used to
carry with it.

Nodes name their children rather than owning them, so a node that two
scenes both want is one node. That is what stops the surfaces from
reinventing each other: there is no "the chat's copy of the settings
ring", there is a settings node and two edges into it.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ui"))
sys.path.insert(0, str(REPO / "src"))

import graph as G  # noqa: E402
from dictatr import runstate  # noqa: E402

BIN = REPO / "bin"
DICTATE = str(BIN / "dictate")

# Scenes this module can build as nodes. Everything else is still the
# surface it always was, in its own process, until it has been written
# here -- see FALLBACK.
BUILT = frozenset({"menu"})

# What to launch for a scene the shell cannot draw yet.
#
# The alternative is what this replaced: shell.open() looked the scene
# name up among the *node* ids, so "chat" matched the leaf you had just
# clicked and the camera zoomed into a node with no children, while
# "settings" matched nothing at all and the click did nothing. Four of
# the menu's entries went nowhere. A half-built graph should hand the
# job back to the program that still does it, not swallow it.
FALLBACK = {
    "chat": [str(BIN / "dictate-chat")],
    "setup": [str(BIN / "dictate-setup")],
    "settings": [str(BIN / "dictate-menu"), "--settings"],
    "file": [str(BIN / "dictate-menu"), "--file"],
}


def _run(*args, detach=False):
    def action():
        subprocess.Popen([DICTATE, *args],
                         start_new_session=detach)
    return action


def _spawn(cmd):
    subprocess.Popen(cmd, start_new_session=True)


def menu_nodes(on_close=None):
    """The root menu, and everything reachable from it.

    The same six choices ui/menu.py has always offered, minus the window:
    what used to be "spawn a process and close" is now an edge, and what
    used to be a submenu is now a node with children like any other.
    """
    live = bool(runstate.live_pid(runstate.LISTEN_PID))
    return [
        G.Node("menu", "Menu", "view-more-symbolic",
               children=("dictate", "clip", "chat", "listen", "more",
                         "cancel")),
        G.Node("dictate", "Dictate at the cursor",
               "audio-input-microphone-symbolic", group="say",
               data={"action": _run("type")}),
        G.Node("clip", "Dictate to the clipboard", "edit-copy-symbolic",
               group="say", data={"action": _run("clip")}),
        G.Node("chat", "Ask the AI", "dictatr-chat-symbolic", group="say",
               data={"scene": "chat"}),
        G.Node("listen", "Always-on capture", "media-record-symbolic",
               group="run", data={"action": _run("listen", "--toggle"),
                                  "on": live}),
        G.Node("more", "More", "view-more-symbolic", group="run",
               children=("file", "gc", "prefs", "setup")),
        G.Node("cancel", "Cancel recording", "process-stop-symbolic",
               group="run", data={"action": _run("cancel")}),

        G.Node("file", "Transcribe an audio file", "folder-music-symbolic",
               data={"scene": "file"}),
        G.Node("gc", "Clean up the archive", "user-trash-symbolic",
               data={"action": _run("gc", "--notify", detach=True)}),
        G.Node("prefs", "Settings", "preferences-system-symbolic",
               data={"scene": "settings"}),
        G.Node("setup", "Set up dictatr", "dictatr-engine-symbolic",
               data={"scene": "setup"}),
    ]


def activate(node, shell):
    """What choosing a leaf does.

    Three kinds: something to run, another scene to open, or nothing —
    and a node that carries neither is a node that has not been wired up
    yet rather than a crash.

    A scene this module has not built yet is not a dead end: it opens the
    program that still owns it. That keeps every menu entry doing what it
    says while the graph grows underneath, and it is what makes deleting
    the old surfaces a decision rather than an accident -- when FALLBACK
    is empty, nothing routes to them any more.
    """
    action = node.data.get("action")
    if action is not None:
        action()
        shell.dismiss()
        return True
    scene = node.data.get("scene")
    if scene is None:
        return False
    if scene in BUILT:
        shell.open(scene)
        return True
    cmd = FALLBACK.get(scene)
    if cmd is None:
        return False
    _spawn(cmd)
    shell.dismiss()
    return True
