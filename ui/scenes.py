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
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ui"))
sys.path.insert(0, str(REPO / "src"))

import graph as G  # noqa: E402
from dictatr import actions, context, deliver, runstate  # noqa: E402
from dictatr.settings import (REGISTRY, settings,  # noqa: E402
                              write_config)

BIN = REPO / "bin"
DICTATE = str(BIN / "dictate")

# Scenes this module can build as nodes. Everything else is still the
# surface it always was, in its own process, until it has been written
# here -- see FALLBACK.
BUILT = frozenset({"menu", "suggest", "settings"})

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
    setter = node.data.get("set")
    if setter is not None:
        # A setting, not an errand. The surface stays up and redraws
        # itself, because the answer to "did that take?" should be the
        # bubble you just pressed rather than a notification about a
        # window you closed.
        setter()
        if node.data.get("back"):
            shell.canvas.back()
        rebuild(shell)
        return True
    if node.data.get("folder"):
        shell.choose_folder(node.data["folder"])
        return True
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


# --- suggest: what to do with the text in front of you ------------------
def _act(action_id, text, arg=""):
    """Run a catalogue action on *text* and deliver the result where the
    words came from.

    Off the main thread, because a model takes seconds and a surface
    that stops answering for seconds is a surface that has crashed as
    far as anyone watching it is concerned. The shell is already on its
    way out by then, which is the right order: delivery types into
    whatever had the selection, and that window needs the focus back
    before anything is typed into it.
    """
    def action():
        if not text:
            deliver.notify("Select some text first", category="errors")
            return
        threading.Thread(target=_run_action, daemon=True,
                         args=(action_id, text, arg)).start()
    return action


def _run_action(action_id, text, arg):
    try:
        out = actions.run(action_id, text, arg)
    except Exception as e:
        deliver.notify(f"Could not do that: {e}", category="errors")
        return
    if out:
        deliver.deliver(out)


def suggest_text():
    """The text this scene is about: whatever is selected, or the
    clipboard. First answer wins, and "" when there is neither."""
    for _label, text in context.gather(["selection", "clipboard"]):
        return text
    return ""


def suggest_nodes(text, picks=None):
    """The suggest ring: a shortlist if the model has one, the head of
    the catalogue if it does not.

    Both shapes are built here rather than one replacing the other in
    place, because a scene is a graph and a graph is cheap. What the
    ring is *about* is the text, which does not change underneath it.
    """
    shortlist = picks is not None
    chosen = picks if shortlist else [
        {"id": a.id, "icon": a.icon, "label": a.label, "arg": ""}
        for a in actions.CATALOGUE[:5]]

    nodes = [G.Node(f"act-{i}", p["label"], p["icon"], group="do",
                    data={"action": _act(p["id"], text, p.get("arg", ""))})
             for i, p in enumerate(chosen)]
    kids = [n.id for n in nodes]

    nodes.append(G.Node("ask-this", "Ask about this",
                        "dictatr-chat-symbolic", group="ask",
                        data={"scene": "chat"}))
    kids.append("ask-this")

    if shortlist:
        # Everything the shortlist left out is one level down, so a bad
        # guess costs a click rather than the whole ring.
        rest = [G.Node(f"all-{i}", a.label, a.icon, group="do",
                       data={"action": _act(a.id, text)})
                for i, a in enumerate(actions.CATALOGUE)]
        nodes.extend(rest)
        nodes.append(G.Node("everything", "Everything else",
                            "view-more-symbolic", group="ask",
                            children=tuple(n.id for n in rest)))
        kids.append("everything")

    title = ("Suggested for this text" if shortlist
             else "What to do with this" if text else "Nothing selected")
    nodes.insert(0, G.Node("suggest", title, "starred-symbolic",
                           children=tuple(kids)))
    return nodes


# --- the roots ----------------------------------------------------------
# A scene the shell can be opened *at*, as opposed to a node it can walk
# to. Built fresh every time: the menu's always-on bubble is only right
# if it is asked again, and the suggest ring is about whatever text is
# in front of you at the moment you ask for it, not the last one.
def open_menu(shell):
    shell.set_scene(G.Graph(menu_nodes()), "menu")


def open_suggest(shell):
    """Open on the catalogue now, and on the model's shortlist when it
    lands.

    Opening on the catalogue immediately is the whole design: a ring
    that waited for a model would be a ring that arrives after you have
    given up. The shortlist replaces it a second or three later, and if
    it never lands this is already useful.
    """
    text = suggest_text()
    shell.set_scene(G.Graph(suggest_nodes(text)), "suggest")
    if not text:
        return

    def landed(picks):
        # Only if this is still the ring being looked at, and still its
        # root: replacing a level someone has already opened would take
        # the choice out from under them.
        if picks and shell.showing and len(shell.canvas.path.ids) == 1 \
                and shell.canvas.path.ids[0] == "suggest":
            shell.set_scene(G.Graph(suggest_nodes(text, picks)), "suggest")
        return False

    def work():
        # Imported here, not at the top: this module stays importable
        # without GTK -- tests/test_scenes.py checks the whole graph
        # without a display, and it can only do that while nothing above
        # needs a toolkit. Everything GTK-shaped in this file is inside
        # a function the shell calls.
        from gi.repository import GLib
        try:
            picks = actions.suggest(text)
        except Exception:
            picks = []
        GLib.idle_add(landed, picks)

    threading.Thread(target=work, daemon=True).start()


def open_settings(shell):
    """Open on what is configured now, and on the server's model lists
    when they arrive -- the same bargain the suggest ring makes."""
    shell.set_scene(G.Graph(settings_nodes()), "settings")

    def landed(models):
        if shell.showing and shell.scene == "settings":
            shell.canvas.refresh(G.Graph(settings_nodes(models)))
        return False

    def work():
        from gi.repository import GLib          # see open_suggest
        models = _models()
        if models is not None:
            GLib.idle_add(landed, models)

    threading.Thread(target=work, daemon=True).start()


def _models():
    """(dictation, ask) model names the backend offers, or None.

    None rather than empty on failure: a ring that emptied its own model
    list because a server was down would be a ring that had thrown the
    setting away.
    """
    try:
        import json
        import urllib.request
        from dictatr.backend import client as backend
        b = backend.get_backend()
        req = urllib.request.Request(f"{b.api_base}/models",
                                     headers=b.headers())
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)["data"]
    except Exception:
        return None
    asr = [m["id"] for m in data if "transcription" in (m.get("labels") or [])]
    llm = [m["id"] for m in data
           if "transcription" not in (m.get("labels") or [])
           and "kokoro" not in m["id"].lower()]
    if settings.whisper.model not in asr:
        asr.insert(0, settings.whisper.model)
    if settings.llm.model not in llm:
        llm.insert(0, settings.llm.model)
    return asr, llm


ROOTS = {"menu": open_menu, "suggest": open_suggest,
         "settings": open_settings}

# How to draw a scene again after something it shows has changed. Only
# the scenes that show mutable state need one: a menu entry does not
# change under you, but the bubble you just toggled has to say so.
GRAPHS = {"menu": lambda: G.Graph(menu_nodes()),
          "settings": lambda: G.Graph(settings_nodes())}


def rebuild(shell):
    make = GRAPHS.get(shell.scene)
    if make is not None:
        shell.canvas.refresh(make())


# --- settings: the same ring, all the way down --------------------------
# There used to be a GtkGrid of dropdowns, spin buttons and switches
# behind a Save button, in a window that looked like nothing else here.
# Two vocabularies for one program is one too many, and the ring is the
# one that answers the pointer where it already is.
#
# No Save: every bubble writes as it is pressed. That is not a shortcut
# but the honest shape -- settings.py resolves every setting on every
# read precisely so a change lands in the running processes at once, and
# a Save button would only be a place for that to go wrong.
PAUSES = (300, 500, 800, 1200, 2000)        # segment pause, ms
QUIETS = (1.0, 1.5, 2.0, 3.0, 5.0)          # finish after quiet, s


def _write(**updates):
    def set_it():
        write_config(updates)
    return set_it


def _toggle(key, on):
    return _write(**{key: not on})


def _switch(node_id, title, icon, key, on, group=""):
    return G.Node(node_id, f"{title}: {'on' if on else 'off'}", icon,
                  group=group, data={"set": _toggle(key, on), "on": on})


def _choice(node_id, title, icon, on, **updates):
    """One entry in a list of alternatives. Choosing backs out to the
    level that asked, because the question has been answered."""
    return G.Node(node_id, title, icon,
                  data={"set": _write(**updates), "on": on, "back": True})


def settings_nodes(models=None):
    """Every setting the window used to hold, as bubbles.

    *models* is (dictation, ask) name lists once the backend has been
    asked. Until then each list is just what is configured now: a ring
    that waited for a server would be a ring you could not open offline.
    """
    asr, llm = models or ([settings.whisper.model], [settings.llm.model])
    ctx = {n.strip() for n in settings.llm.context.split(",") if n.strip()}
    archived = settings.storage.enabled

    nodes = [
        _switch("set-speak", "Speak answers aloud", "audio-speakers-symbolic",
                "speak_answers", settings.llm.speak, group="chat"),
        _switch("set-details", "Show the chat's working",
                "document-properties-symbolic", "chat_details",
                settings.llm.details, group="chat"),
        G.Node("set-context", "What Ask may read", "edit-find-symbolic",
               group="chat", children=("ctx-selection", "ctx-clipboard")),
        G.Node("set-models", "Models", "dictatr-engine-symbolic",
               group="engine", children=("mdl-asr", "mdl-llm")),
        G.Node("set-timing", "Listening", "alarm-symbolic",
               group="engine", children=("tm-pause", "tm-quiet")),
        G.Node("set-archive", "Archive", "folder-symbolic", group="disk",
               children=("arch-on", "arch-dir")),
        G.Node("set-notify", "Notifications",
               "preferences-desktop-notification-symbolic", group="disk",
               children=tuple(f"ntf-{k}" for k, _l, _i in NOTIFY)),
    ]
    kids = tuple(n.id for n in nodes)

    nodes += [
        G.Node("ctx-selection",
               f"Selected text: {'yes' if 'selection' in ctx else 'no'}",
               "edit-select-all-symbolic",
               data={"set": _context(ctx, "selection"),
                     "on": "selection" in ctx}),
        G.Node("ctx-clipboard",
               f"Clipboard: {'yes' if 'clipboard' in ctx else 'no'}",
               "edit-copy-symbolic",
               data={"set": _context(ctx, "clipboard"),
                     "on": "clipboard" in ctx}),

        G.Node("mdl-asr", "Dictation model", "audio-input-microphone-symbolic",
               children=tuple(f"asr-{i}" for i in range(len(asr)))),
        G.Node("mdl-llm", "Ask model", "dictatr-chat-symbolic",
               children=tuple(f"llm-{i}" for i in range(len(llm)))),

        G.Node("tm-pause", "Segment pause", "appointment-soon-symbolic",
               children=tuple(f"pause-{i}" for i in range(len(PAUSES)))),
        G.Node("tm-quiet", "Finish after quiet",
               "document-open-recent-symbolic",
               children=tuple(f"quiet-{i}" for i in range(len(QUIETS)))),

        # Not a plain boolean: the archive key holds "off" or the folder
        # it writes to, so turning it on has to name somewhere to go.
        G.Node("arch-on", f"Keep recordings: {'on' if archived else 'off'}",
               "media-record-symbolic",
               data={"on": archived,
                     "set": _write(archive="off" if archived
                                   else REGISTRY["archive"].default)}),
        G.Node("arch-dir",
               settings.storage.base if archived else "Choose a folder…",
               "folder-open-symbolic", data={"folder": "archive"}),
    ]
    nodes += [_switch(f"ntf-{key}", label, icon, f"notify_{key}",
                      getattr(settings.notify, key))
              for key, label, icon in NOTIFY]
    nodes += [_choice(f"asr-{i}", name, "dictatr-engine-symbolic",
                      name == settings.whisper.model, model=name)
              for i, name in enumerate(asr)]
    nodes += [_choice(f"llm-{i}", name, "dictatr-chat-symbolic",
                      name == settings.llm.model, llm_model=name)
              for i, name in enumerate(llm)]
    nodes += [_choice(f"pause-{i}", f"{ms} ms", "appointment-soon-symbolic",
                      ms == settings.vad.silence_duration_ms, silence_ms=ms)
              for i, ms in enumerate(PAUSES)]
    nodes += [_choice(f"quiet-{i}", f"{s:g} s", "document-open-recent-symbolic",
                      abs(s - settings.vad.idle_s) < 0.01, idle_s=s)
              for i, s in enumerate(QUIETS)]

    nodes.insert(0, G.Node("settings", "Settings",
                           "preferences-system-symbolic", children=kids))
    return nodes


NOTIFY = (("state", "State", "media-record-symbolic"),
          ("delivery", "Delivery", "dictatr-typing-symbolic"),
          ("answers", "Answers", "dictatr-chat-symbolic"),
          ("toggles", "Toggles", "emblem-system-symbolic"),
          ("errors", "Errors", "error-symbolic"))


def _context(have, name):
    """Ask's context is one comma-separated key, so a toggle on it is a
    set operation rather than a boolean."""
    want = have - {name} if name in have else have | {name}
    return _write(ask_context=",".join(sorted(want)))


def archive_chosen(path):
    """A folder came back from the picker. Turning the archive on by
    choosing where it goes is one act, not two."""
    write_config({"archive": path or REGISTRY["archive"].default})
