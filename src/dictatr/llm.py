"""Ask-mode helpers: chat completion and spoken answers via Lemonade.

Mirrors listenr's llm_processor shape (Lemonade OpenAI-compatible HTTP) so a
future `listenr dictate` merge can swap these for listenr's own client.
"""

import json
import shutil
import subprocess
import tempfile
import urllib.request

from .settings import settings

SYSTEM_PROMPT = (
    "You are a concise local voice assistant. The user is speaking, not "
    "typing. Answer briefly in plain text: no markdown, no lists unless "
    "asked, at most a few sentences."
)


def chat(question: str, context: list[dict] | None = None) -> str:
    # One merged system message: some chat templates (gpt-oss) mishandle or
    # drop a second system entry. Thinking is disabled for latency — voice
    # answers need seconds, not a hidden reasoning chain (templates without
    # an enable_thinking knob simply ignore the kwarg).
    system = SYSTEM_PROMPT
    if context:
        notes = "\n".join(f"- [{c['date']}] {c['text']}" for c in context)
        system += (
            "\nYou can consult entries the user previously dictated on this "
            "machine; use them when relevant:\n" + notes)
    req = urllib.request.Request(
        f"{settings.whisper.api_base}/chat/completions",
        data=json.dumps({
            "model": settings.llm.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
            "max_tokens": 2048,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return (json.load(r)["choices"][0]["message"]["content"] or "").strip()


def speak(text: str) -> None:
    """Synthesize *text* with Kokoro and play it. Best-effort."""
    try:
        req = urllib.request.Request(
            f"{settings.whisper.api_base}/audio/speech",
            data=json.dumps({
                "model": settings.llm.tts_model,
                "input": text[:1500],
                "voice": settings.llm.tts_voice,
            }).encode(),
            headers={"Content-Type": "application/json"},
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
