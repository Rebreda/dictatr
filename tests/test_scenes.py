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
