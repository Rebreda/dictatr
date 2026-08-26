import json

from dictatr import tools
from dictatr.settings import settings


def test_remember_and_load(tmp_path):
    settings.storage.base = str(tmp_path)
    assert tools.t_remember("  ") == "(nothing to remember)"
    tools.t_remember("User's name is Bob")
    tools.t_remember("Runs Fedora KDE")
    assert tools.load_memories() == ["User's name is Bob", "Runs Fedora KDE"]
    # file is plain JSONL with timestamps
    row = json.loads(tools.memories_path().read_text().splitlines()[0])
    assert row["fact"] == "User's name is Bob"
    assert "timestamp" in row


def test_find_files_rejects_bad_patterns():
    assert tools.t_find_files("") == "(invalid pattern)"
    assert tools.t_find_files("x" * 100) == "(invalid pattern)"


def test_current_time_runs():
    out = tools.t_current_time()
    assert "20" in out  # contains a year


def test_registry_shapes():
    schemas, executors = tools.registry()
    names = {s["function"]["name"] for s in schemas}
    assert {"current_time", "find_files", "remember", "calendar"} <= names
    assert set(executors) == names
    for s in schemas:
        assert s["type"] == "function"
        assert "parameters" in s["function"]
