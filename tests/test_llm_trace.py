"""The trace llm.chat narrates to on_step.

An answer arrives with none of its working visible — which context was
read, what was recalled, which tools ran. The chat card shows that when
"show the chat's working" is on and archives it either way, so what the
loop reports is worth pinning down."""

import json

import pytest

from dictatr import llm


@pytest.fixture
def server(monkeypatch):
    """Scripted /chat/completions replies, plus a bare desktop: no
    selection, no clipboard, no memories, no focused app, so a test only
    sees the steps it set up."""
    box = {"replies": []}

    def fake_post(payload, timeout=180.0):
        box["last"] = payload
        return box["replies"].pop(0)

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    monkeypatch.setattr(llm.desktop, "gather", lambda names: [])
    monkeypatch.setattr(llm.toolbox, "load_memories", lambda: [])
    monkeypatch.setattr(llm.runstate, "read_app", lambda: None)
    monkeypatch.setattr(llm.toolbox, "registry", lambda: ([], {}))
    monkeypatch.setattr(
        llm.backend, "get_backend",
        lambda: type("B", (), {"cap": lambda self, k: type(
            "C", (), {"model": "test-model", "base": "", "headers":
                      lambda self: {}})()})())
    return box


def steps_of(box, question="hi", **kw):
    got = []
    answer = llm.chat(question, on_step=lambda k, d: got.append((k, d)), **kw)
    return answer, got


def test_the_model_is_always_named(server):
    server["replies"] = [{"content": "hello"}]
    answer, got = steps_of(server)
    assert answer == "hello"
    assert ("model", "test-model (no tools this turn)") in got


def test_recalled_notes_are_reported(server):
    server["replies"] = [{"content": "ok"}]
    _, got = steps_of(server, context=[{"date": "2026-08-01",
                                        "text": "the invoice numbering"}])
    assert ("recall", "[2026-08-01] the invoice numbering") in got


def test_a_tool_call_reports_its_arguments_and_answer(server, monkeypatch):
    monkeypatch.setattr(llm.toolbox, "registry",
                        lambda: ([{"name": "current_time"}],
                                 {"current_time": lambda a: "14:32"}))
    server["replies"] = [
        {"tool_calls": [{"id": "1", "function": {
            "name": "current_time", "arguments": json.dumps({"tz": "local"})}}]},
        {"content": "It is 14:32."},
    ]
    answer, got = steps_of(server, "what time is it")
    assert answer == "It is 14:32."
    assert ("tool", "current_time(tz='local') → 14:32") in got


def test_reasoning_is_reported_when_the_server_sends_it(server):
    server["replies"] = [{"content": "42", "reasoning_content": " weighing it "}]
    _, got = steps_of(server)
    assert ("thinking", "weighing it") in got


def test_desktop_context_is_reported(server, monkeypatch):
    monkeypatch.setattr(llm.desktop, "gather",
                        lambda names: [("Text the user has selected", "abc")])
    monkeypatch.setattr(llm.runstate, "read_app", lambda: "kate")
    server["replies"] = [{"content": "ok"}]
    _, got = steps_of(server)
    assert ("context", "text the user has selected (3 chars)") in got
    assert ("context", "focused app: kate") in got


def test_no_callback_is_the_default_and_costs_nothing(server):
    server["replies"] = [{"content": "quiet"}]
    assert llm.chat("hi") == "quiet"
