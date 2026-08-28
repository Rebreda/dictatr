"""Probe for an already-running server without starting anything."""

import urllib.request

# Current lemond default port first, then the legacy python server's.
CANDIDATES = ("http://localhost:13305", "http://localhost:8080")
TIMEOUT = 1.5


def probe(api_base: str, key: str | None = None,
          timeout: float = TIMEOUT) -> bool:
    """True when <root>/v1/health answers; api_base may carry /api/v1."""
    root = api_base.split("/api/")[0].rstrip("/")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    req = urllib.request.Request(f"{root}/v1/health", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def detect(configured: str | None = None,
           key: str | None = None) -> str | None:
    """First reachable api_base: the configured URL, then the default
    ports. Bare host URLs gain the /api/v1 prefix Lemonade serves."""
    candidates = ([configured] if configured else []) + list(CANDIDATES)
    for base in candidates:
        base = base.rstrip("/")
        api = base if "/api/" in base or base.endswith("/v1") \
            else f"{base}/api/v1"
        if probe(api, key):
            return api
    return None
