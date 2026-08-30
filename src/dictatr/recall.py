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

from . import runstate
from .backend import client as backend
from .settings import settings

# Nudges, not rankings: enough to break ties between rows the embedding
# scores alike, never enough to float an unrelated row to the top.
CONCEPT_BOOST = 0.12   # a query word matches the row's concept tags
APP_BOOST = 0.08       # the row was dictated in the app in front of you


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _cache_path(base: Path) -> Path:
    # JSONL, appended to: a dictation adds one line instead of rewriting
    # every vector it already knows. One file per embedding model, so
    # switching models cannot mix vector spaces.
    return base / "cache" / f"embeddings-{_slug(settings.llm.embed_model)}.jsonl"


def _load_cache(path: Path) -> dict:
    cache = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    cache[row["k"]] = row["v"]
                except (json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        pass
    return cache


def _append_cache(path: Path, vectors: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for k, v in vectors.items():
            f.write(json.dumps({"k": k, "v": v}) + "\n")


def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def embed(texts: list[str]) -> list[list[float]]:
    cap = backend.get_backend().cap("embed")
    req = urllib.request.Request(
        f"{cap.base}/embeddings",
        data=json.dumps({
            "model": cap.model, "input": texts,
        }).encode(),
        headers={"Content-Type": "application/json", **cap.headers()},
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
                if text and not r.get("gc"):  # skip quarantined/purged rows
                    rows.append({"text": text,
                                 "date": (r.get("timestamp") or "")[:10],
                                 "uuid": r.get("uuid", ""),
                                 "app": (r.get("meta") or {}).get("app"),
                                 "categories": r.get("categories") or []})
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
    cache = _load_cache(cache_file)

    missing = [r["text"] for r in rows if _key(r["text"]) not in cache]
    # De-dup while preserving order; embed only what the cache lacks.
    missing = list(dict.fromkeys(missing))
    if missing:
        fresh = {_key(t): v for t, v in zip(missing, embed(missing))}
        cache.update(fresh)
        _append_cache(cache_file, fresh)

    qvec = embed([query])[0]
    # Concept boost: rows whose LLM-extracted tags appear in the query get a
    # nudge, so the concept index shapes results without a second search.
    qwords = set(re.findall(r"[a-z0-9-]+", query.lower()))
    here = runstate.read_app()
    scored = []
    for r in rows:
        if _key(r["text"]) not in cache:
            continue
        score = _cosine(qvec, cache[_key(r["text"])])
        if qwords & set(r["categories"]):
            score += CONCEPT_BOOST
        if here and r.get("app") == here:
            score += APP_BOOST
        scored.append({**r, "score": score})
    scored.sort(key=lambda r: r["score"], reverse=True)
    return [r for r in scored[:k] if r["score"] >= min_score]
