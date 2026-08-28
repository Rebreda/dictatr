import io
import json
import urllib.request
from pathlib import Path

import pytest

from dictatr.backend import client, detect, lemond
from dictatr.settings import settings


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, payload):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
        super().__init__(payload)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --- provider resolution precedence ----------------------------------

def test_lemonade_url_forces_system(monkeypatch):
    # env wins over everything; detection must not even run
    monkeypatch.setattr(detect, "detect",
                        lambda *a, **k: pytest.fail("detection ran"))
    monkeypatch.setattr(lemond, "alive",
                        lambda *a, **k: pytest.fail("managed probe ran"))
    b = client.resolve(cfg={"backend": "custom"},
                       env={"LEMONADE_URL": "http://box:9999/api/v1"})
    assert (b.kind, b.api_base, b.api_key) == \
        ("system", "http://box:9999/api/v1", None)


def test_config_provider_beats_detection(monkeypatch):
    monkeypatch.setattr(detect, "detect",
                        lambda *a, **k: pytest.fail("detection ran"))
    b = client.resolve(cfg={"backend": "custom",
                            "chat_url": "https://api.x/v1"}, env={})
    assert b.kind == "custom"


def test_auto_prefers_running_managed(monkeypatch):
    monkeypatch.setattr(lemond, "alive", lambda *a, **k: True)
    monkeypatch.setattr(lemond, "api_base",
                        lambda: "http://127.0.0.1:4242/api/v1")
    monkeypatch.setattr(lemond, "api_key", lambda: "sek")
    b = client.resolve(cfg={}, env={})
    assert (b.kind, b.api_base, b.api_key) == \
        ("managed", "http://127.0.0.1:4242/api/v1", "sek")


def test_auto_falls_back_to_detected_system(monkeypatch):
    monkeypatch.setattr(lemond, "alive", lambda *a, **k: False)
    monkeypatch.setattr(client.bdetect, "detect",
                        lambda *a, **k: "http://localhost:13305/api/v1")
    b = client.resolve(cfg={}, env={})
    assert (b.kind, b.api_base) == ("system", "http://localhost:13305/api/v1")


def test_auto_falls_back_to_custom_then_default(monkeypatch):
    monkeypatch.setattr(lemond, "alive", lambda *a, **k: False)
    monkeypatch.setattr(client.bdetect, "detect", lambda *a, **k: None)
    b = client.resolve(cfg={}, env={"OPENAI_BASE_URL": "https://oai/v1/",
                                    "OPENAI_API_KEY": "ok"})
    assert (b.kind, b.api_base, b.api_key) == ("custom", "https://oai/v1", "ok")
    # nothing configured, nothing running: today's default URL
    b = client.resolve(cfg={}, env={})
    assert (b.kind, b.api_base) == ("system", settings.whisper.api_base)


def test_configured_managed_autostarts(monkeypatch):
    started = []
    monkeypatch.setattr(lemond, "alive", lambda *a, **k: False)
    monkeypatch.setattr(lemond, "start", lambda *a, **k: started.append(1))
    monkeypatch.setattr(lemond, "api_base",
                        lambda: "http://127.0.0.1:4242/api/v1")
    monkeypatch.setattr(lemond, "api_key", lambda: "sek")
    b = client.resolve(cfg={"backend": "managed"}, env={})
    assert b.kind == "managed" and started == [1]
    started.clear()
    client.resolve(cfg={"backend": "managed"}, env={}, allow_start=False)
    assert started == []


# --- detection --------------------------------------------------------

def test_detect_probes_in_order(monkeypatch):
    tried = []

    def fake_urlopen(req, timeout=None):
        tried.append(req.full_url)
        if req.full_url.startswith("http://localhost:8080"):
            return FakeResponse({"status": "ok"})
        raise OSError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert detect.detect() == "http://localhost:8080/api/v1"
    assert tried == ["http://localhost:13305/v1/health",
                     "http://localhost:8080/v1/health"]


def test_detect_configured_url_first(monkeypatch):
    def fake_urlopen(req, timeout=None):
        if req.full_url == "http://box:7777/v1/health":
            return FakeResponse({"status": "ok"})
        raise OSError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert detect.detect("http://box:7777/api/v1") == "http://box:7777/api/v1"


def test_detect_none_when_nothing_answers(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise OSError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert detect.detect() is None


# --- client URL / header construction --------------------------------

def test_managed_urls_and_headers(monkeypatch):
    monkeypatch.setattr(lemond, "alive", lambda *a, **k: True)
    monkeypatch.setattr(lemond, "api_base",
                        lambda: "http://127.0.0.1:4242/api/v1")
    monkeypatch.setattr(lemond, "api_key", lambda: "sek")
    b = client.resolve(cfg={}, env={})
    assert b.headers() == {"Authorization": "Bearer sek"}
    monkeypatch.setattr(client.Backend, "health",
                        lambda self, timeout=5: {"websocket_port": 9001})
    assert b.realtime_ws_urls("Moonshine-Medium-Streaming") == [
        "ws://127.0.0.1:4242/api/v1/realtime"
        "?model=Moonshine-Medium-Streaming&api_key=sek",
        "ws://localhost:9001/realtime"
        "?model=Moonshine-Medium-Streaming&api_key=sek",
    ]


def test_custom_capability_overrides():
    cfg = {"backend": "custom", "asr_url": "https://asr.example/v1/",
           "asr_model": "whisper-1", "chat_key": "ck"}
    env = {"OPENAI_BASE_URL": "https://oai.example/v1",
           "OPENAI_API_KEY": "ok"}
    b = client.resolve(cfg=cfg, env=env)
    asr = b.cap("asr")
    assert (asr.base, asr.model) == ("https://asr.example/v1", "whisper-1")
    assert asr.headers() == {"Authorization": "Bearer ok"}  # key inherited
    chat = b.cap("chat")
    assert chat.base == "https://oai.example/v1"
    assert chat.headers() == {"Authorization": "Bearer ck"}
    assert chat.model == settings.llm.model
    tts = b.cap("tts")
    assert (tts.base, tts.model) == ("https://oai.example/v1",
                                     settings.llm.tts_model)


def test_system_ignores_custom_url_overrides(monkeypatch):
    b = client.resolve(cfg={"backend": "system",
                            "backend_url": "http://box:1234/api/v1",
                            "asr_url": "https://other/v1",
                            "asr_model": "whisper-1"}, env={})
    assert b.cap("asr").base == "http://box:1234/api/v1"
    assert b.cap("asr").model == "whisper-1"  # model overrides do apply


# --- the legacy-compat guarantee -------------------------------------

def test_legacy_lemonade_url_produces_todays_urls(monkeypatch):
    env = {"LEMONADE_URL": "http://localhost:8080/api/v1"}
    b = client.resolve(cfg={}, env=env)
    assert b.kind == "system" and b.headers() == {}
    assert (f"{b.api_base}/audio/transcriptions" ==
            "http://localhost:8080/api/v1/audio/transcriptions")
    assert (f"{b.api_base}/chat/completions" ==
            "http://localhost:8080/api/v1/chat/completions")
    assert f"{b.root}/v1/health" == "http://localhost:8080/v1/health"
    monkeypatch.setattr(client.Backend, "health",
                        lambda self, timeout=5: {"websocket_port": 8001})
    assert b.realtime_ws_urls("Moonshine-Medium-Streaming") == [
        "ws://localhost:8080/api/v1/realtime?model=Moonshine-Medium-Streaming",
        "ws://localhost:8001/realtime?model=Moonshine-Medium-Streaming",
    ]

    # health down: same hardcoded 8001 fallback as today's engine.py
    def boom(self, timeout=5):
        raise OSError("down")
    monkeypatch.setattr(client.Backend, "health", boom)
    assert (b.realtime_ws_urls("Moonshine-Medium-Streaming")[1] ==
            "ws://localhost:8001/realtime?model=Moonshine-Medium-Streaming")


# --- lemond SSE progress parsing -------------------------------------

def test_pull_parses_sse_progress(monkeypatch):
    stream = b"\n".join([
        b"event: progress",
        b'data: {"file": "a.gguf", "bytes_downloaded": 10, "percent": 1}',
        b"",
        b'data: {"file": "a.gguf", "bytes_downloaded": 500, "percent": 50}',
        b"this line is not sse",
        b"data: not json either",
        b'data: {"file": "a.gguf", "bytes_downloaded": 1000, "percent": 100}',
        b"data: [DONE]",
        b'data: {"never": "seen"}',
    ])
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data)
        return FakeResponse(stream)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    seen = []
    last = lemond.pull("some-model", on_progress=seen.append,
                       base="http://localhost:13305/api/v1", key="sek")
    assert captured["url"] == "http://localhost:13305/api/v1/pull"
    assert captured["auth"] == "Bearer sek"
    assert captured["body"] == {"model": "some-model", "stream": True}
    assert [e["percent"] for e in seen] == [1, 50, 100]
    assert last["percent"] == 100


# --- the version pin and packaging must agree ------------------------

def test_pinned_version_matches_packaging():
    env_file = (Path(__file__).resolve().parent.parent
                / "packaging" / "lemond-version.env")
    pins = dict(
        line.split("=", 1) for line in env_file.read_text().splitlines()
        if "=" in line and not line.startswith("#"))
    assert pins["LEMOND_VERSION"] == lemond.PINNED_VERSION
    assert pins["LEMOND_SHA256"] == lemond.PINNED_SHA256
    assert lemond.PINNED_VERSION in lemond.TARBALL_URL
