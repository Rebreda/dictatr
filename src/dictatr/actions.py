"""Things dictatr can do to a piece of text, and the model's opinion of
which ones fit.

A closed catalogue, deliberately. The model chooses, orders and fills in
arguments; it never invents a command. That keeps a suggestion ring fast
(one small completion), predictable (every bubble is an action you have
seen before), and safe (nothing the model returns is executed as code).

The result of an action is text, delivered the ordinary way. Typing
replaces a selection in most editors, so "rewrite this" lands where the
words came from.
"""

import json
import re

from . import llm, runstate, tools

MAX_INPUT = 6000


class Action:
    def __init__(self, aid, label, icon, prompt, arg_hint=""):
        self.id, self.label, self.icon = aid, label, icon
        self.prompt, self.arg_hint = prompt, arg_hint


CATALOGUE = [
    Action("summarize", "Summarise", "view-list-text-symbolic",
           "Summarise the text in at most three sentences. Reply with the "
           "summary only."),
    Action("rewrite", "Rewrite", "document-edit-symbolic",
           "Rewrite the text {arg}. Keep the meaning and the language. "
           "Reply with the rewritten text only.",
           "how to rewrite it, e.g. 'more formally', 'shorter', 'as bullet "
           "points', 'in plain English'"),
    Action("reply", "Draft a reply", "mail-reply-sender-symbolic",
           "Draft a reply to this message. Match its register, keep it "
           "short. Reply with the draft only."),
    Action("todo", "Pull out tasks", "view-task-symbolic",
           "List the actionable tasks in the text, one per line, each "
           "starting with '- '. Reply with the list only."),
    Action("explain", "Explain", "help-about-symbolic",
           "Explain the text plainly in a few sentences: what it is and "
           "what matters about it. Reply with the explanation only."),
    Action("translate", "Translate", "accessories-dictionary-symbolic",
           "Translate the text into {arg}. Reply with the translation only.",
           "the target language"),
    Action("fix", "Fix grammar", "tools-check-spelling-symbolic",
           "Correct spelling, grammar and punctuation. Change nothing "
           "else, keep the voice. Reply with the corrected text only."),
]
BY_ID = {a.id: a for a in CATALOGUE}


def run(action_id: str, text: str, arg: str = "") -> str:
    """Perform one catalogue action and return its text."""
    action = BY_ID.get(action_id)
    if action is None:
        return ""
    if action_id == "remember":
        return tools.t_remember(text)
    system = action.prompt.replace("{arg}", arg or "")
    return llm.complete(system, text[:MAX_INPUT], max_tokens=800)


SUGGEST_PROMPT = (
    "You pick which actions suit a piece of text the user is looking at. "
    "Choose the 3 or 4 most useful from this list, best first:\n{menu}\n"
    "Reply with ONLY a JSON array, each item "
    '{{"id": "<action id>", "arg": "<argument or empty>", '
    '"label": "<3 words at most, what it will do>"}}. '
    "No prose, no code fence."
)


def _menu() -> str:
    lines = []
    for a in CATALOGUE:
        hint = f" (arg: {a.arg_hint})" if a.arg_hint else ""
        lines.append(f"- {a.id}: {a.label}{hint}")
    return "\n".join(lines)


def suggest(text: str, timeout: float = 25.0) -> list[dict]:
    """The model's shortlist for *text*: [{id, arg, label}].

    Returns [] on any trouble; the caller already has the static ring on
    screen and simply keeps it.
    """
    app = runstate.read_app()
    where = f"\nThe user is in {app}." if app else ""
    raw = llm.complete(SUGGEST_PROMPT.format(menu=_menu()) + where,
                       text[:MAX_INPUT], max_tokens=300, timeout=timeout)
    match = re.search(r"\[.*\]", raw, re.S)   # models like to add a preamble
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        action = BY_ID.get(str(item.get("id", "")))
        if action is None:
            continue
        label = str(item.get("label") or action.label).strip()[:28]
        out.append({"id": action.id, "arg": str(item.get("arg") or "").strip(),
                    "label": label, "icon": action.icon})
    return out[:4]
