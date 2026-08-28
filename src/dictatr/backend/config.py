"""Backend provider selection.

Three provider kinds (docs/backend-design.md): managed (dictatr's own
lemond instance), system (an existing Lemonade on this machine), custom
(OpenAI-compatible endpoints configured per capability). Selection
sources, strongest first: LEMONADE_URL (the legacy contract: system
provider at exactly that URL), the config file's flat `backend` keys,
then auto-resolution in client.py.

Config keys (flat, in ~/.config/dictatr/config.toml):
  backend       "managed" | "system" | "custom" (unset = auto)
  backend_url   server URL for the system provider
  <cap>_url / <cap>_key / <cap>_model  per-capability custom endpoints,
  cap in asr / chat / tts / embed. OPENAI_BASE_URL / OPENAI_API_KEY
  fill whatever the custom provider leaves unset.
"""

import os
from dataclasses import dataclass

from ..settings import raw_config, settings

CAPS = ("asr", "chat", "tts", "embed")


def default_model(cap: str) -> str:
    # Model fallbacks reuse today's settings groups, so their existing
    # env vars and config keys (model, llm_model, ...) keep working.
    return {
        "asr": settings.whisper.model,
        "chat": settings.llm.model,
        "tts": settings.llm.tts_model,
        "embed": settings.llm.embed_model,
    }[cap]


@dataclass
class BackendConfig:
    provider: str | None      # None = auto-resolve
    url: str | None           # configured server URL (system provider)
    forced: bool              # LEMONADE_URL set: skip detection entirely
    custom_base: str | None   # default base for custom capabilities
    custom_key: str | None
    caps: dict                # cap -> {"url", "key", "model"} (or Nones)

    @property
    def has_custom(self) -> bool:
        return bool(self.custom_base
                    or any(c["url"] for c in self.caps.values()))


def load(cfg: dict | None = None, env=None) -> BackendConfig:
    """Read provider config; *cfg*/*env* are injectable for tests."""
    cfg = raw_config() if cfg is None else cfg
    env = os.environ if env is None else env

    caps = {}
    for cap in CAPS:
        caps[cap] = {
            "url": (str(cfg.get(f"{cap}_url") or "").rstrip("/") or None),
            "key": str(cfg.get(f"{cap}_key") or "") or None,
            "model": str(cfg.get(f"{cap}_model") or "") or None,
        }

    if env.get("LEMONADE_URL"):
        # Legacy contract: the URL is used verbatim, nothing else applies.
        return BackendConfig("system", env["LEMONADE_URL"], True,
                             None, None, caps)

    return BackendConfig(
        provider=str(cfg.get("backend") or "") or None,
        url=str(cfg.get("backend_url") or cfg.get("api_base") or "") or None,
        forced=False,
        custom_base=(env.get("OPENAI_BASE_URL") or "").rstrip("/") or None,
        custom_key=env.get("OPENAI_API_KEY") or None,
        caps=caps,
    )
