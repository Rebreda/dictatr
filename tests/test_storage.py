import json
import wave

from dictatr.storage import patch_manifest_record, save_recording

LISTENR_FIELDS = {
    "uuid", "timestamp", "audio_path", "raw_transcription",
    "corrected_transcription", "is_improved", "categories",
    "whisper_model", "llm_model", "duration_s", "sample_rate",
}


def test_save_recording_matches_listenr_schema(tmp_path):
    pcm = b"\x00\x01" * 16000  # 1s
    rec = save_recording(pcm, "hello world", storage_base=tmp_path,
                         whisper_model="test-model", meta={"mode": "dictate"})

    assert LISTENR_FIELDS <= set(rec)
    assert rec["duration_s"] == 1.0
    assert rec["source"] == "dictatr"
    assert rec["meta"]["mode"] == "dictate"
    assert "os" in rec["meta"] and "host" in rec["meta"]

    with wave.open(rec["audio_path"]) as w:
        assert w.getframerate() == 16000
        assert w.getnframes() == 16000

    lines = (tmp_path / "manifest.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["uuid"] == rec["uuid"]


def test_patch_manifest_record(tmp_path):
    rec = save_recording(b"\x00\x01" * 800, "note", storage_base=tmp_path)
    manifest = tmp_path / "manifest.jsonl"

    assert patch_manifest_record(manifest, rec["uuid"],
                                 {"categories": ["code", "todo"]})
    row = json.loads(manifest.read_text().splitlines()[0])
    assert row["categories"] == ["code", "todo"]
    assert row["raw_transcription"] == "note"  # untouched fields survive

    assert not patch_manifest_record(manifest, "nope", {"categories": []})
