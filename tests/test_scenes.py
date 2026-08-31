"""The scene graph, and the promise that every menu entry goes somewhere.

scenes.py imports graph and runstate and nothing else, so the wiring can
be checked here rather than by clicking eleven bubbles on a live display.
"""

import os

import pytest

import scenes


NODES = {n.id: n for n in scenes.menu_nodes()}


def test_every_named_child_exists():
    """Children are named rather than owned, which is what lets two
    scenes share a node -- and also what lets a typo point at nothing."""
    for node in NODES.values():
        for child in node.children:
            assert child in NODES, f"{node.id} names a missing child {child}"


def test_every_leaf_does_something():
    """A leaf with neither an action nor a scene is a bubble that looks
    live and is not."""
    for node in NODES.values():
        if node.children:
            continue
        assert ("action" in node.data or "scene" in node.data), \
            f"{node.id} is a leaf that does nothing"


@pytest.mark.parametrize("node", [n for n in NODES.values()
                                  if "scene" in n.data],
                         ids=lambda n: n.id)
def test_every_scene_resolves(node):
    """The regression this guards: shell.open() used to look a scene name
    up among the *node* ids, so "chat" matched the leaf just clicked and
    zoomed into a node with no children, while "settings" matched nothing
    and the click did nothing at all. Four entries went nowhere.

    A scene must therefore be one of two things and never a third: built
    here as nodes, or handed to the program that still owns it.
    """
    scene = node.data["scene"]
    assert scene in scenes.BUILT or scene in scenes.FALLBACK, \
        f"{node.id} opens scene {scene!r}, which is neither built nor spawned"


def test_a_scene_is_not_both():
    """Once a scene is built, its fallback has to go, or the shell keeps
    spawning the process it just replaced."""
    both = scenes.BUILT & set(scenes.FALLBACK)
    assert not both, f"built and still spawned: {sorted(both)}"


@pytest.mark.parametrize("scene,cmd", sorted(scenes.FALLBACK.items()))
def test_fallback_commands_are_runnable(scene, cmd):
    """Spawning is only a safety net if the thing on the end of it runs."""
    launcher = cmd[0]
    assert os.path.isfile(launcher), f"{scene}: {launcher} does not exist"
    assert os.access(launcher, os.X_OK), f"{scene}: {launcher} not executable"


class FakeShell:
    def __init__(self):
        self.opened = []
        self.dismissed = 0

    def open(self, scene):
        self.opened.append(scene)

    def dismiss(self):
        self.dismissed += 1


def test_an_unbuilt_scene_spawns_and_gets_out_of_the_way(monkeypatch):
    """The chat is not a node yet, so choosing it must open the chat --
    not zoom the camera at a leaf and leave the menu sitting there."""
    launched = []
    monkeypatch.setattr(scenes.subprocess, "Popen",
                        lambda cmd, **kw: launched.append(cmd))
    shell = FakeShell()
    assert scenes.activate(NODES["chat"], shell) is True
    assert launched and launched[0] == scenes.FALLBACK["chat"]
    assert shell.opened == []          # never mistaken for a node id
    assert shell.dismissed == 1        # the menu goes away behind it


def test_a_built_scene_moves_within_the_shell(monkeypatch):
    """The point of the graph: a scene it owns is a zoom, not a process."""
    launched = []
    monkeypatch.setattr(scenes.subprocess, "Popen",
                        lambda cmd, **kw: launched.append(cmd))
    shell = FakeShell()
    node = scenes.G.Node("x", "X", "i", data={"scene": "menu"})
    assert scenes.activate(node, shell) is True
    assert shell.opened == ["menu"]
    assert launched == []


def test_an_action_still_runs_and_dismisses(monkeypatch):
    ran = []
    shell = FakeShell()
    node = scenes.G.Node("x", "X", "i", data={"action": lambda: ran.append(1)})
    assert scenes.activate(node, shell) is True
    assert ran == [1] and shell.dismissed == 1


# --- the suggest scene --------------------------------------------------
# Built from the text in front of you rather than written out, so what is
# worth checking is that both shapes it can take are whole graphs.

def _suggest(picks=None):
    return {n.id: n for n in scenes.suggest_nodes("some selected text",
                                                  picks)}


PICKS = [{"id": "summarise", "icon": "view-list-symbolic",
          "label": "Summarise", "arg": ""},
         {"id": "rewrite", "icon": "document-edit-symbolic",
          "label": "Rewrite", "arg": "shorter"}]


@pytest.mark.parametrize("picks", [None, PICKS],
                         ids=["catalogue", "shortlist"])
def test_suggest_is_a_whole_graph(picks):
    nodes = _suggest(picks)
    assert "suggest" in nodes
    for node in nodes.values():
        for child in node.children:
            assert child in nodes, f"{node.id} names a missing child {child}"


@pytest.mark.parametrize("picks", [None, PICKS],
                         ids=["catalogue", "shortlist"])
def test_every_suggest_leaf_does_something(picks):
    for node in _suggest(picks).values():
        if node.children:
            continue
        assert ("action" in node.data or "scene" in node.data), \
            f"{node.id} is a leaf that does nothing"


def test_the_shortlist_keeps_the_rest_within_reach():
    """A model's five guesses replace the catalogue on the ring, and the
    catalogue moves one level down -- a bad guess should cost a click,
    not the whole ring."""
    nodes = _suggest(PICKS)
    assert "everything" in nodes
    rest = nodes["everything"].children
    assert len(rest) == len(scenes.actions.CATALOGUE)


def test_a_suggest_scene_with_no_text_still_opens():
    """Nothing selected is a ring that says so, not an empty one: the
    catalogue is still there for whatever you select next."""
    nodes = {n.id: n for n in scenes.suggest_nodes("")}
    assert nodes["suggest"].children
    assert "Nothing" in nodes["suggest"].title


def test_suggest_is_a_built_scene():
    """It used to be its own program (bin/dictate-suggest ran
    ui/menu.py --suggest). If it leaves BUILT, that shim goes back to
    opening a surface that no longer exists."""
    assert "suggest" in scenes.BUILT
    assert "suggest" not in scenes.FALLBACK


# --- the settings scene -------------------------------------------------
# It was a window of dropdowns and switches behind a Save button. As a
# ring there is no Save, so what has to hold is that pressing a bubble
# writes exactly one thing and writes it correctly.

@pytest.fixture
def written(monkeypatch):
    """Every config write the scene makes, without making any."""
    log = []
    monkeypatch.setattr(scenes, "write_config", lambda u: log.append(u))
    return log


def _settings():
    return {n.id: n for n in scenes.settings_nodes()}


def test_the_settings_scene_is_a_whole_graph():
    nodes = _settings()
    assert "settings" in nodes
    for node in nodes.values():
        for child in node.children:
            assert child in nodes, f"{node.id} names a missing child {child}"


def test_every_settings_leaf_writes_something():
    """A bubble that looks like a setting and sets nothing is worse than
    one that is not there."""
    for node in _settings().values():
        if node.children or node.id == "settings":
            continue
        assert ("set" in node.data or "folder" in node.data), \
            f"{node.id} is a settings leaf that changes nothing"


def test_a_toggle_writes_the_other_value(written):
    node = _settings()["set-speak"]
    node.data["set"]()
    assert written == [{"speak_answers": not node.data["on"]}]


def test_choosing_writes_the_value_and_backs_out(written):
    """A choice answers a question, so the level that asked it is done."""
    node = _settings()["pause-0"]
    assert node.data["back"] is True
    node.data["set"]()
    assert written == [{"silence_ms": scenes.PAUSES[0]}]


def test_the_archive_toggle_never_writes_a_boolean(written):
    """The archive key holds "off" or the folder it writes to. A plain
    negation would put True in it, which is neither."""
    _settings()["arch-on"].data["set"]()
    assert len(written) == 1
    value = written[0]["archive"]
    assert value == "off" or "/" in value


def test_what_ask_may_read_stays_a_comma_separated_key(written):
    nodes = _settings()
    nodes["ctx-selection"].data["set"]()
    (update,) = written
    assert set(update) == {"ask_context"}
    parts = [p for p in update["ask_context"].split(",") if p]
    assert all(p in ("selection", "clipboard") for p in parts)
    assert ("selection" in parts) is not nodes["ctx-selection"].data["on"]


def test_the_model_lists_survive_a_server_that_is_not_there():
    """Offline, the ring still offers what is configured -- emptying the
    list would be throwing the setting away."""
    nodes = _settings()
    assert nodes["mdl-asr"].children and nodes["mdl-llm"].children


def test_settings_is_a_built_scene():
    assert "settings" in scenes.BUILT
    assert "settings" not in scenes.FALLBACK
