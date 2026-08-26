"""Concept tagging: build a searchable higher-level index over dictations.

After a dictation is archived, a fast LLM call extracts a handful of topic
tags (work, code, notes, todo, question, ...). Tags land in the manifest
row's `categories` field — the slot listenr's schema already defines for
exactly this — and in an aggregate sidecar index (cache/concepts.json,
tag -> [uuid...]) so recall can shortlist by concept without embedding
anything. Best-effort: a failed tagging never blocks delivery.
"""

import json
import re
from pathlib import Path

from . import llm
from .settings import settings
from .storage import patch_manifest_record

TAG_PROMPT = (
    "Extract 2-6 short lowercase topic tags for this dictated note. Prefer "
    "these when they fit: work, code, notes, todo, action, question, idea, "
    "personal, meeting. Add at most two free-form subject tags (single "
    "words). Respond with ONLY the comma-separated tags."
)


def index_path(base: Path) -> Path:
    return base / "cache" / "concepts.json"


def extract_tags(text: str, timeout: float = 30.0) -> list[str]:
    raw = llm.complete(TAG_PROMPT, text, max_tokens=60, timeout=timeout)
    tags = [re.sub(r"[^a-z0-9-]", "", t.strip().lower())
            for t in raw.split(",")]
    return [t for t in tags if 1 < len(t) <= 24][:6]


def annotate(record: dict) -> list[str]:
    """Tag one archived recording; update its manifest row and the index."""
    base = Path(settings.storage.base).expanduser()
    text = record.get("corrected_transcription") or record.get(
        "raw_transcription") or ""
    if not text.strip():
        return []
    tags = extract_tags(text)
    if not tags:
        return []

    patch_manifest_record(base / "manifest.jsonl", record["uuid"],
                          {"categories": tags})

    idx_file = index_path(base)
    try:
        index = json.loads(idx_file.read_text())
    except (OSError, json.JSONDecodeError):
        index = {}
    for tag in tags:
        ids = index.setdefault(tag, [])
        if record["uuid"] not in ids:
            ids.append(record["uuid"])
    idx_file.parent.mkdir(parents=True, exist_ok=True)
    idx_file.write_text(json.dumps(index, ensure_ascii=False))
    return tags


def sweep(limit: int = 25) -> int:
    """Backfill tags for archived rows that have none yet. Meant to run when
    the LLM is already warm (e.g. right after an ask answer). Returns the
    number of rows tagged."""
    base = Path(settings.storage.base).expanduser()
    tagged = 0
    try:
        lines = (base / "manifest.jsonl").read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        if tagged >= limit:
            break
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("categories") or record.get("gc"):
            continue
        try:
            if annotate(record):
                tagged += 1
        except Exception:
            break  # LLM unavailable; try again next sweep
    return tagged
