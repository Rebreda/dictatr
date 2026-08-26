import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dictatr import cleanup
from dictatr.settings import settings
from dictatr.storage import save_recording


def _row(tmp_path, text, mode="listen", seconds=2.0, ts=None):
    pcm = b"\x00\x01" * int(16000 * seconds)
    rec = save_recording(pcm, text, storage_base=tmp_path,
                         meta={"mode": mode})
    if ts is not None:
        from dictatr.storage import patch_manifest_record
        patch_manifest_record(tmp_path / "manifest.jsonl", rec["uuid"],
                              {"timestamp": ts.isoformat()})
    return rec


def test_judge_rules():
    listen = {"meta": {"mode": "listen"}, "duration_s": 2.0}
    assert cleanup.judge({**listen, "raw_transcription": ""}) == "empty"
    assert cleanup.judge({**listen, "raw_transcription": "Thank you."},
                         ) == "hallucination"
    assert cleanup.judge({**listen, "duration_s": 0.5,
                          "raw_transcription": "hm"}) == "too_short"
    assert cleanup.judge(
        {**listen, "raw_transcription": "la " * 20}) == "repetition"
    assert cleanup.judge({**listen, "duration_s": 23.0,
                          "raw_transcription": "Thank you."}) == "sparse"
    assert cleanup.judge({**listen, "raw_transcription":
                          "remember to check the oven"}) is None
    # interactive rows only get the gentle rules
    dictate = {"meta": {"mode": "dictate"}, "duration_s": 0.5}
    assert cleanup.judge({**dictate, "raw_transcription": "Thank you."}) is None
    assert cleanup.judge({**dictate, "raw_transcription": "yes"}) is None
    # pending / already-collected rows are untouchable
    assert cleanup.judge({"meta": {"mode": "listen",
                          "pending_transcription": True},
                          "raw_transcription": ""}) is None
    assert cleanup.judge({"gc": {"action": "quarantined"},
                          "raw_transcription": ""}) is None


def test_duplicate_within_window():
    t0 = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    prev = {"norm": "the same line again", "timestamp": t0.isoformat()}
    row = {"meta": {"mode": "listen"}, "duration_s": 2.0,
           "raw_transcription": "The same line, again!",
           "timestamp": (t0 + timedelta(minutes=5)).isoformat()}
    assert cleanup.judge(row, prev) == "duplicate"
    row["timestamp"] = (t0 + timedelta(minutes=30)).isoformat()
    assert cleanup.judge(row, prev) is None


def test_sweep_quarantines_and_restore(tmp_path):
    settings.storage.base = str(tmp_path)
    junk = _row(tmp_path, "Thank you.")
    good = _row(tmp_path, "note the deploy finished cleanly")

    summary = cleanup.sweep()
    assert summary["quarantined"] == {"hallucination": 1}
    assert summary["kept"] == 1

    rows = {r["uuid"]: r for r in map(
        json.loads, (tmp_path / "manifest.jsonl").read_text().splitlines())}
    assert rows[junk["uuid"]]["gc"]["reason"] == "hallucination"
    trash_path = Path(rows[junk["uuid"]]["audio_path"])
    assert trash_path.exists() and "trash" in trash_path.parts
    assert not Path(junk["audio_path"]).exists()
    assert rows[good["uuid"]].get("gc") is None

    # second sweep is a no-op; quarantined rows aren't re-judged
    assert cleanup.sweep()["quarantined"] == {}

    assert cleanup.restore(junk["uuid"])
    rows = {r["uuid"]: r for r in map(
        json.loads, (tmp_path / "manifest.jsonl").read_text().splitlines())}
    assert rows[junk["uuid"]]["gc"] is None
    assert Path(rows[junk["uuid"]]["audio_path"]).exists()
    assert "trash" not in Path(rows[junk["uuid"]]["audio_path"]).parts


def test_dry_run_touches_nothing(tmp_path):
    settings.storage.base = str(tmp_path)
    junk = _row(tmp_path, "Thank you.")
    summary = cleanup.sweep(dry_run=True)
    assert summary["quarantined"] == {"hallucination": 1}
    assert Path(junk["audio_path"]).exists()
    row = json.loads((tmp_path / "manifest.jsonl").read_text())
    assert row.get("gc") is None


def test_purge_deletes_expired_trash(tmp_path):
    settings.storage.base = str(tmp_path)
    junk = _row(tmp_path, "Thank you.")
    cleanup.sweep()
    # backdate the quarantine stamp past the purge window
    from dictatr.storage import patch_manifest_record
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    patch_manifest_record(tmp_path / "manifest.jsonl", junk["uuid"],
                          {"gc": {"action": "quarantined",
                                  "reason": "hallucination", "at": old}})
    assert cleanup.purge() == 1
    row = json.loads((tmp_path / "manifest.jsonl").read_text())
    assert row["gc"]["action"] == "purged"
    assert not Path(row["audio_path"]).exists()
