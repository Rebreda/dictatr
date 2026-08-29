"""Ask-mode desktop context: which sources are read, and how they reach
the prompt. wl-paste is stubbed; the point is the selection logic."""

from dictatr import context


def test_gather_only_named_sources(monkeypatch):
    monkeypatch.setitem(context.SOURCES, "selection",
                        ("Text the user has selected", lambda: "the deploy"))
    monkeypatch.setitem(context.SOURCES, "clipboard",
                        ("The user's clipboard", lambda: "unrelated"))
    got = context.gather(["selection"])
    assert got == [("Text the user has selected", "the deploy")]
    assert context.gather([]) == []
    assert context.gather(["nonsense"]) == []


def test_gather_skips_empty_and_truncates(monkeypatch):
    monkeypatch.setitem(context.SOURCES, "selection", ("Sel", lambda: "   "))
    monkeypatch.setitem(context.SOURCES, "clipboard",
                        ("Clip", lambda: "x" * (context.MAX_CHARS + 500)))
    got = context.gather(["selection", "clipboard"])
    assert [label for label, _ in got] == ["Clip"]
    assert len(got[0][1]) == context.MAX_CHARS


def test_prompt_section_empty_when_nothing_found():
    assert context.prompt_section([]) == ""
    section = context.prompt_section([("Sel", "the deploy")])
    assert "the deploy" in section and "Sel" in section
