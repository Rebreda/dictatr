import json

from dictatr import recall
from dictatr.settings import settings
from dictatr.storage import save_recording


def fake_embed_factory(calls):
    vectors = {
        "my name is bob": [1.0, 0.0, 0.0],
        "the deploy pipeline": [0.0, 1.0, 0.0],
        "groceries list": [0.0, 0.0, 1.0],
    }

    def fake_embed(texts):
        calls.append(list(texts))
        return [vectors.get(t.lower(), [0.5, 0.5, 0.0]) for t in texts]
    return fake_embed


def setup_archive(tmp_path):
    settings.storage.base = str(tmp_path)
    for text in ["My name is Bob", "The deploy pipeline", "Groceries list"]:
        save_recording(b"\x00\x01" * 800, text, storage_base=tmp_path)


def test_search_ranks_by_similarity(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(recall, "embed", fake_embed_factory(calls))
    setup_archive(tmp_path)

    top = recall.search("my name is bob", k=2)
    assert top and top[0]["text"] == "My name is Bob"
    assert top[0]["score"] > 0.99


def test_embeddings_are_cached(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(recall, "embed", fake_embed_factory(calls))
    setup_archive(tmp_path)

    recall.search("my name is bob")
    recall.search("the deploy pipeline")
    # first call embeds the 3 rows + query; second embeds only its query
    assert [len(c) for c in calls] == [3, 1, 1]
    assert (tmp_path / "cache").exists()


def test_concept_boost(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(recall, "embed", fake_embed_factory(calls))
    settings.storage.base = str(tmp_path)
    a = save_recording(b"\x00\x01" * 800, "alpha note", storage_base=tmp_path)
    b = save_recording(b"\x00\x01" * 800, "beta note", storage_base=tmp_path)
    # identical embeddings; only b carries a matching concept tag
    from dictatr.storage import patch_manifest_record
    patch_manifest_record(tmp_path / "manifest.jsonl", b["uuid"],
                          {"categories": ["deploy"]})

    top = recall.search("deploy", k=2, min_score=0.0)
    assert top[0]["text"] == "beta note"
    assert top[0]["score"] > top[1]["score"]
