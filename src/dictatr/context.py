"""What the user is looking at, for ask mode.

"Summarise this" only works if the model can see what "this" is, so a
question can carry the text you have selected and what is on the
clipboard. Sources are opt-in by name (settings.llm.context), because
this is the difference between a question about your words and a
question about whatever happened to be in the clipboard.

Selection is the interesting one: highlighting text is a deliberate act
that says "this is what I mean", and on Wayland it is already sitting in
the primary selection.

Screen contents are deliberately not here. Reading the screen means a
screenshot through the portal plus a vision model on every question,
which is a different feature with a different cost.
"""

import shutil
import subprocess

MAX_CHARS = 4000   # a paragraph or two of context, not a whole document


def _paste(primary: bool) -> str:
    if not shutil.which("wl-paste"):
        return ""
    cmd = ["wl-paste", "--no-newline"]
    if primary:
        cmd.append("--primary")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


SOURCES = {
    "selection": ("Text the user has selected", lambda: _paste(True)),
    "clipboard": ("The user's clipboard", lambda: _paste(False)),
}


def gather(names) -> list[tuple[str, str]]:
    """[(label, text)] for each requested source that has something."""
    out = []
    for name in names:
        entry = SOURCES.get(name)
        if entry is None:
            continue
        label, read = entry
        text = (read() or "").strip()
        if text:
            out.append((label, text[:MAX_CHARS]))
    return out


def prompt_section(items) -> str:
    """The system-prompt block for gathered context, empty when none."""
    if not items:
        return ""
        # (an empty block would invite the model to invent one)
    parts = ["\nWhat the user is looking at right now. Use it when the "
             "question refers to \"this\", \"that\" or \"the selection\"; "
             "ignore it otherwise."]
    for label, text in items:
        parts.append(f"--- {label} ---\n{text}")
    return "\n".join(parts)
