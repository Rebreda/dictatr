"""The action catalogue and how the model's shortlist is read back.

llm.complete is stubbed: what matters is that a closed catalogue stays
closed and that a sloppy answer still yields usable bubbles."""

import pytest

from dictatr import actions


@pytest.fixture
def answer(monkeypatch):
    box = {}

    def fake(system, user, **kw):
        box["system"], box["user"] = system, user
        return box["reply"]
    monkeypatch.setattr(actions.llm, "complete", fake)
    return box


def test_suggest_reads_a_plain_array(answer):
    answer["reply"] = ('[{"id": "todo", "arg": "", "label": "Extract tasks"},'
                       ' {"id": "rewrite", "arg": "shorter", "label": "Tighten"}]')
    got = actions.suggest("some text")
    assert [p["id"] for p in got] == ["todo", "rewrite"]
    assert got[1]["arg"] == "shorter"
    assert got[0]["icon"] == actions.BY_ID["todo"].icon


def test_suggest_survives_preamble_and_unknown_ids(answer):
    answer["reply"] = ('Sure! Here you go:\n```json\n'
                       '[{"id": "rm -rf", "arg": "", "label": "nope"},'
                       ' {"id": "explain", "arg": "", "label": "Explain"}]\n```')
    got = actions.suggest("x")
    assert [p["id"] for p in got] == ["explain"]   # invented ids are dropped


def test_suggest_gives_up_quietly(answer):
    answer["reply"] = "I could not decide."
    assert actions.suggest("x") == []


def test_suggest_caps_the_ring(answer):
    answer["reply"] = "[" + ",".join(
        '{"id": "explain", "arg": "", "label": "E"}' for _ in range(9)) + "]"
    assert len(actions.suggest("x")) == 4


def test_run_fills_the_argument_in(answer):
    answer["reply"] = "rewritten"
    assert actions.run("rewrite", "hello", "as bullet points") == "rewritten"
    assert "as bullet points" in answer["system"]
    assert answer["user"] == "hello"


def test_run_ignores_an_action_that_does_not_exist(answer):
    answer["reply"] = "should not be used"
    assert actions.run("sudo", "hello") == ""
