"""Ask-mode helpers: chat completion, tool calling, spoken answers.

Mirrors listenr's llm_processor shape (Lemonade OpenAI-compatible HTTP) so a
future `listenr dictate` merge can swap these for listenr's own client.
"""

import json
import shutil
import subprocess
import tempfile
import urllib.request

from . import context as desktop
from . import runstate
from . import tools as toolbox
from .backend import client as backend
from .settings import settings

SYSTEM_PROMPT = (
    "You are a concise local voice assistant. The user is speaking, not "
    "typing. Answer briefly in plain text: no markdown, no lists unless "
    "asked, at most a few sentences.\n"
    "You have tools. ALWAYS call a tool instead of guessing when the "
    "question involves the current time or date (current_time), files on "
    "this computer (find_files), upcoming events (calendar), or when the "
    "user tells you something worth keeping for the future (remember). "
    "Never invent times, dates or file paths."
)


def complete(system: str, user: str, max_tokens: int = 512,
             timeout: float = 60.0) -> str:
    """One-shot completion with thinking disabled (latency)."""
    cap = backend.get_backend().cap("chat")
    req = urllib.request.Request(
        f"{cap.base}/chat/completions",
        data=json.dumps({
            "model": cap.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode(),
        headers={"Content-Type": "application/json", **cap.headers()},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.load(r)["choices"][0]["message"]["content"] or "").strip()


def _post_chat(payload: dict, timeout: float = 180.0) -> dict:
    cap = backend.get_backend().cap("chat")
    req = urllib.request.Request(
        f"{cap.base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **cap.headers()},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]


def _desktop_context() -> str:
    names = [n.strip() for n in settings.llm.context.split(",") if n.strip()]
    section = desktop.prompt_section(desktop.gather(names)) if names else ""
    app = runstate.read_app()
    if app:
        section += (f"\nThe user is working in {app} right now. Let it "
                    "colour how you answer (a question asked in an editor "
                    "is usually about code); never mention it unasked.")
    return section


def _system_prompt(context: list[dict] | None) -> str:
    system = SYSTEM_PROMPT + _desktop_context()
    memories = toolbox.load_memories()
    if memories:
        system += ("\nLasting facts you remembered about the user:\n"
                   + "\n".join(f"- {m}" for m in memories))
    if context:
        notes = "\n".join(f"- [{c['date']}] {c['text']}" for c in context)
        system += (
            "\nYou can consult entries the user previously dictated on this "
            "machine; use them when relevant:\n" + notes)
    return system


def chat(question: str, context: list[dict] | None = None,
         history: list[dict] | None = None) -> str:
    """Tool-calling conversation loop: the model may consult local tools
    (time, file search, calendar, remember) before answering. *history*
    is prior turns as {"role", "content"} dicts (the chat window's
    running conversation)."""
    schemas, executors = toolbox.registry()
    messages = [
        {"role": "system", "content": _system_prompt(context)},
        *(history or []),
        {"role": "user", "content": question},
    ]
    for _ in range(4):
        msg = _post_chat({
            "model": backend.get_backend().cap("chat").model,
            "messages": messages,
            "tools": schemas,
            "max_tokens": 2048,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip()
        messages.append({"role": "assistant",
                         "content": msg.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = (executors[name](args) if name in executors
                      else f"(unknown tool {name})")
            messages.append({"role": "tool",
                             "tool_call_id": call.get("id", ""),
                             "content": result})
    return "(tool loop did not converge)"


def speak(text: str) -> None:
    """Synthesize *text* with Kokoro and play it. Best-effort."""
    try:
        cap = backend.get_backend().cap("tts")
        req = urllib.request.Request(
            f"{cap.base}/audio/speech",
            data=json.dumps({
                "model": cap.model,
                "input": text[:1500],
                "voice": settings.llm.tts_voice,
            }).encode(),
            headers={"Content-Type": "application/json", **cap.headers()},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            audio = r.read()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            path = f.name
        if shutil.which("ffplay"):
            subprocess.run(["ffplay", "-nodisp", "-autoexit",
                            "-loglevel", "quiet", path], check=False)
        else:
            subprocess.run(["pw-play", path], check=False)
    except Exception:
        pass  # a silent answer is fine; the text is on the clipboard
