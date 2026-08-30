"""What `dictate file` does when the engine will not answer.

Found by running it: a backend holding the dictation model pinned --
which is the state dictatr's own setup leaves it in -- refuses to load
the batch model beside it, and the 409 escaped as a stack trace. A
transcription that cannot happen is an ordinary outcome, not a defect.
"""

import io
import json
import urllib.error

import pytest

from dictatr import batch


def http_error(code, body):
    return urllib.error.HTTPError(
        "http://x/audio/transcriptions", code, "Conflict", {},
        io.BytesIO(json.dumps(body).encode()))


PINNED = {"error": {"code": "slots_pinned_error",
                    "message": "All loaded models of type "
                               "standard/transcription are pinned."}}


def test_a_pinned_slot_is_explained(monkeypatch):
    monkeypatch.setattr(batch.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            http_error(409, PINNED)))
    with pytest.raises(ConnectionError) as e:
        batch.transcribe_bytes(b"", "clip.wav", model="Whisper-Large-v3-Turbo")
    said = str(e.value)
    assert "pinned" in said
    assert "Whisper-Large-v3-Turbo" in said      # which model could not load
    assert "backend status" in said              # and how to find out more


def test_any_other_http_error_still_speaks(monkeypatch):
    monkeypatch.setattr(batch.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            http_error(500, {"error": {"message": "boom"}})))
    with pytest.raises(ConnectionError, match="boom"):
        batch.transcribe_bytes(b"", "clip.wav")


def test_a_body_that_is_not_json_still_speaks(monkeypatch):
    err = urllib.error.HTTPError("http://x", 502, "Bad Gateway", {},
                                 io.BytesIO(b"<html>nginx</html>"))
    monkeypatch.setattr(batch.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(err))
    with pytest.raises(ConnectionError, match="502"):
        batch.transcribe_bytes(b"", "clip.wav")


def test_an_unreachable_engine_names_the_address(monkeypatch):
    monkeypatch.setattr(batch.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("refused")))
    with pytest.raises(ConnectionError, match="cannot reach"):
        batch.transcribe_bytes(b"", "clip.wav")


def test_cmd_file_reports_and_exits_one(monkeypatch, capsys):
    """The whole point: a line on stderr and a non-zero exit, never a
    traceback."""
    from dictatr import cli
    monkeypatch.setattr(cli, "transcribe_file",
                        lambda p: (_ for _ in ()).throw(
                            ConnectionError("engine is busy")))
    monkeypatch.setattr(cli.dlv, "notify", lambda *a, **k: None)
    assert cli.cmd_file("clip.wav") == 1
    assert "engine is busy" in capsys.readouterr().err
