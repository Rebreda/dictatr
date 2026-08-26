"""Archive garbage collection (`dictatr gc`): quarantine junk, purge old trash.

Always-on listening archives everything the VAD hears, so the archive
accumulates breath-trigger clips, ASR hallucinations and TV/music loops.
This sweep scores manifest rows and moves the losers' audio into
<base>/trash/YYYY-MM-DD/ — quarantine, not deletion, because a
false-positive "hallucination" that was really a mumbled note is
unrecoverable fine-tuning data. `--restore UID` undoes; trash older than
gc.purge_days is deleted for good on the next run.

Interactive rows (mode dictate/ask) get gentle rules — the user chose to
speak, so a deliberate one-word note survives. Unattended listen rows get
the aggressive rules. Rows flagged meta.pending_transcription (archived
while Lemonade was down) are never touched.
"""

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .settings import settings
from .storage import patch_manifest_record

# Phantom phrases whisper-family models emit on breath/noise segments.
HALLUCINATIONS = {
    "thank you", "thank you very much", "thanks for watching",
    "thank you for watching", "you", "bye", "okay", "yeah", "so",
    "uh", "um", "hmm", "oh", "silence", "the end", "subscribe",
}
REPEAT_MIN_WORDS = 12
REPEAT_MAX_UNIQUE = 0.25
DUP_WINDOW_S = 600


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def judge(row: dict, prev_listen: dict | None = None) -> str | None:
    """Reason this row is junk, or None to keep it.

    *prev_listen* is the previous kept listen-mode row, for duplicate
    detection (TV/music loops repeat the same line minutes apart)."""
    if row.get("gc") or (row.get("meta") or {}).get("pending_transcription"):
        return None
    text = (row.get("corrected_transcription")
            or row.get("raw_transcription") or "").strip()
    if not text:
        return "empty"

    norm = _norm(text)
    words = norm.split()
    if len(words) >= REPEAT_MIN_WORDS and \
            len(set(words)) / len(words) <= REPEAT_MAX_UNIQUE:
        return "repetition"

    if (row.get("meta") or {}).get("mode") != "listen":
        return None
    duration = row.get("duration_s") or 0.0
    if norm in HALLUCINATIONS and duration < 3.0:
        return "hallucination"
    if duration < settings.gc.min_duration_s and \
            len(words) < settings.gc.min_words:
        return "too_short"
    if prev_listen and norm and norm == prev_listen["norm"]:
        try:
            gap = abs((datetime.fromisoformat(row["timestamp"])
                       - datetime.fromisoformat(prev_listen["timestamp"]))
                      .total_seconds())
        except (KeyError, ValueError):
            gap = DUP_WINDOW_S + 1
        if gap <= DUP_WINDOW_S:
            return "duplicate"
    return None


def _rows(manifest: Path) -> list[dict]:
    rows = []
    try:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return rows


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sweep(dry_run: bool = False, purge_days: float | None = None) -> dict:
    """Quarantine junk rows, then purge expired trash. Returns a summary:
    {"quarantined": {reason: count}, "purged": n, "kept": n}."""
    base = Path(settings.storage.base).expanduser()
    manifest = base / "manifest.jsonl"
    quarantined: dict[str, int] = {}
    kept = 0
    prev_listen = None

    for row in _rows(manifest):
        reason = judge(row, prev_listen)
        if reason is None:
            kept += 1
            if not row.get("gc") and \
                    (row.get("meta") or {}).get("mode") == "listen":
                text = (row.get("corrected_transcription")
                        or row.get("raw_transcription") or "")
                prev_listen = {"norm": _norm(text),
                               "timestamp": row.get("timestamp", "")}
            continue
        quarantined[reason] = quarantined.get(reason, 0) + 1
        if dry_run:
            print(f"would quarantine {row['uuid']} ({reason}): "
                  f"{(row.get('raw_transcription') or '')[:60]!r}")
            continue
        src = Path(row["audio_path"])
        dst = base / "trash" / src.parent.name / src.name
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
        patch_manifest_record(manifest, row["uuid"], {
            "audio_path": str(dst),
            "gc": {"action": "quarantined", "reason": reason, "at": _now()},
        })

    purged = 0 if dry_run else purge(purge_days)
    return {"quarantined": quarantined, "purged": purged, "kept": kept}


def purge(purge_days: float | None = None) -> int:
    """Delete trash audio older than *purge_days* (settings.gc.purge_days
    by default). The manifest row survives with gc.action="purged"."""
    base = Path(settings.storage.base).expanduser()
    manifest = base / "manifest.jsonl"
    days = settings.gc.purge_days if purge_days is None else purge_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    purged = 0
    for row in _rows(manifest):
        gc = row.get("gc") or {}
        if gc.get("action") != "quarantined":
            continue
        try:
            at = datetime.fromisoformat(gc["at"])
        except (KeyError, ValueError):
            continue
        if at > cutoff:
            continue
        Path(row["audio_path"]).unlink(missing_ok=True)
        patch_manifest_record(manifest, row["uuid"], {
            "gc": {**gc, "action": "purged", "purged_at": _now()},
        })
        purged += 1
    return purged


def restore(uid: str) -> bool:
    """Move a quarantined clip back into audio/ and clear its gc mark."""
    base = Path(settings.storage.base).expanduser()
    manifest = base / "manifest.jsonl"
    for row in _rows(manifest):
        if row.get("uuid") != uid:
            continue
        gc = row.get("gc") or {}
        if gc.get("action") != "quarantined":
            print(f"{uid}: not quarantined (gc={gc.get('action')})")
            return False
        src = Path(row["audio_path"])
        dst = base / "audio" / src.parent.name / src.name
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
        return patch_manifest_record(manifest, uid, {
            "audio_path": str(dst), "gc": None,
        })
    print(f"{uid}: not found")
    return False
