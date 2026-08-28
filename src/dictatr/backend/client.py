"""The provider seam: every server call resolves through a Backend.

Resolution order: the LEMONADE_URL contract, then the configured
provider, then auto (a managed lemond already running > a detected
system server > custom endpoints > today's default URL). Resolution is
cached per process; get_backend(refresh=True) re-resolves.
"""

import json
import urllib.request
from dataclasses import dataclass, field

from ..settings import settings
from . import config as bconfig
from . import detect as bdetect
from . import lemond


@dataclass
class Capability:
    base: str
    key: str | None
    model: str

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}


@dataclass
class Backend:
    kind: str                  # "managed" | "system" | "custom"
    api_base: str
    api_key: str | None = None
    cap_overrides: dict = field(default_factory=dict)

    @property
    def root(self) -> str:
        return self.api_base.split("/api/")[0]

    def headers(self) -> dict:
        return ({"Authorization": f"Bearer {self.api_key}"}
                if self.api_key else {})

    def cap(self, name: str) -> Capability:
        """Per-capability endpoint (asr/chat/tts/embed): configured
        override, else this backend's base; models fall back to the
        settings groups."""
        o = self.cap_overrides.get(name) or {}
        return Capability(
            base=o.get("url") or self.api_base,
            key=o.get("key") or self.api_key,
            model=o.get("model") or bconfig.default_model(name))

    def health(self, timeout: float = 5) -> dict:
        req = urllib.request.Request(f"{self.root}/v1/health",
                                     headers=self.headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def realtime_ws_urls(self, model: str | None = None) -> list[str]:
        """Prefer the /realtime proxy on the ASR base; fall back to the
        dedicated websocket port advertised by /v1/health (the legacy
        python server's layout)."""
        cap = self.cap("asr")
        qs = f"?model={model or cap.model}"
        if cap.key:
            qs += f"&api_key={cap.key}"  # lemond auths websockets by query
        proxied = cap.base.replace("http://", "ws://") \
                          .replace("https://", "wss://")
        try:
            port = self.health().get("websocket_port", 8001)
        except Exception:
            port = 8001
        return [f"{proxied}/realtime{qs}",
                f"ws://localhost:{port}/realtime{qs}"]


def _models_only(caps: dict) -> dict:
    # url/key overrides are a custom-provider feature; model names apply
    # to every provider.
    return {c: {"model": v.get("model")} for c, v in caps.items()}


def _managed(caps: dict) -> Backend:
    return Backend("managed", lemond.api_base(), lemond.api_key(),
                   cap_overrides=_models_only(caps))


def _custom(c: bconfig.BackendConfig) -> Backend:
    base = (c.custom_base or c.url
            or next((v["url"] for v in c.caps.values() if v["url"]), None)
            or settings.whisper.api_base)
    return Backend("custom", base, c.custom_key, cap_overrides=c.caps)


def resolve(cfg: dict | None = None, env=None,
            allow_start: bool = True) -> Backend:
    c = bconfig.load(cfg, env)
    if c.forced:  # LEMONADE_URL: exactly today's behavior, byte for byte
        return Backend("system", c.url, cap_overrides=_models_only(c.caps))
    if c.provider == "system":
        base = c.url or bdetect.detect() or settings.whisper.api_base
        return Backend("system", base, cap_overrides=_models_only(c.caps))
    if c.provider == "custom":
        return _custom(c)
    if c.provider == "managed":
        if allow_start and not lemond.alive():
            lemond.start()
        return _managed(c.caps)
    # Auto: prefer our own instance, then anything already running,
    # then custom endpoints, then today's default URL.
    if lemond.alive():
        return _managed(c.caps)
    if base := bdetect.detect(c.url):
        return Backend("system", base, cap_overrides=_models_only(c.caps))
    if c.has_custom:
        return _custom(c)
    return Backend("system", settings.whisper.api_base,
                   cap_overrides=_models_only(c.caps))


_active: Backend | None = None


def get_backend(refresh: bool = False) -> Backend:
    global _active
    if _active is None or refresh:
        _active = resolve()
    return _active
