import json

from dictatr import concepts, llm
from dictatr.settings import settings
from dictatr.storage import save_recording


def test_annotate_tags_manifest_and_index(tmp_path, monkeypatch):
    settings.storage.base = str(tmp_path)
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: "Code, TODO, deploy!, x")
    rec = save_recording(b"\x00\x01" * 800, "fix the deploy script",
                         storage_base=tmp_path)

    tags = concepts.annotate(rec)
    assert tags == ["code", "todo", "deploy"]  # normalized, junk dropped

    row = json.loads((tmp_path / "manifest.jsonl").read_text().splitlines()[0])
    assert row["categories"] == ["code", "todo", "deploy"]

    index = json.loads(concepts.index_path(tmp_path).read_text())
    assert rec["uuid"] in index["deploy"]


def test_annotate_empty_text_is_noop(tmp_path, monkeypatch):
    settings.storage.base = str(tmp_path)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "notes")
    assert concepts.annotate({"uuid": "x", "raw_transcription": "  "}) == []
