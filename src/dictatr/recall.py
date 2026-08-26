"""Semantic recall over the dictation archive, for ask mode.

Mirrors listenr's categorize.py approach — every transcript is embedded once
and cached in a sidecar under the archive's cache/ dir — but uses Lemonade's
/embeddings endpoint (nomic-embed-text, ~75 MB GGUF) instead of a local
sentence-transformers/torch stack, so the only cost is one HTTP call per
new transcript. At dictation-archive scale (hundreds of short texts) the
cosine search is plain Python; no vector database needed.
"""

import hashlib
import json
import math
import re
import urllib.request
from pathlib import Path

from .settings import settings


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _cache_path(base: Path) -> Path:
    return base / "cache" / f"embeddings-{_slug(settings.llm.embed_model)}.json"


def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def embed(texts: list[str]) -> list[list[float]]:
    req = urllib.request.Request(
        f"{settings.whisper.api_base}/embeddings",
        data=json.dumps({
            "model": settings.llm.embed_model, "input": texts,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)["data"]
    return [d["embedding"] for d in data]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _manifest_rows(base: Path) -> list[dict]:
    rows = []
    try:
        with open(base / "manifest.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = (r.get("corrected_transcription")
                        or r.get("raw_transcription") or "").strip()
                if text:
                    rows.append({"text": text,
                                 "date": (r.get("timestamp") or "")[:10]})
    except OSError:
        pass
    return rows


def search(query: str, k: int = 4, min_score: float = 0.45) -> list[dict]:
    """Top-k archived dictations relevant to *query*: [{date, text, score}]."""
    base = Path(settings.storage.base).expanduser()
    rows = _manifest_rows(base)
    if not rows:
        return []

    cache_file = _cache_path(base)
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, json.JSONDecodeError):
        cache = {}

    missing = [r["text"] for r in rows if _key(r["text"]) not in cache]
    # De-dup while preserving order; embed only what the cache lacks.
    missing = list(dict.fromkeys(missing))
    if missing:
        for text, vec in zip(missing, embed(missing)):
            cache[_key(text)] = vec
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))

    qvec = embed([query])[0]
    scored = [
        {**r, "score": _cosine(qvec, cache[_key(r["text"])])}
        for r in rows if _key(r["text"]) in cache
    ]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return [r for r in scored[:k] if r["score"] >= min_score]
