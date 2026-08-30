"""Which bubble replaces which.

The rules are small but easy to get backwards, and getting them
backwards is invisible in a test that only checks "a notification went
out": state chatter must reuse one bubble, everything else must pop
fresh, and the stale state bubble must be closed on the way.
"""

import pytest

from dictatr import deliver
from dictatr.dbus import DBusError


class FakeBus:
    """Records calls and hands back notification ids in order."""

    def __init__(self, ids=(11, 12, 13), fail=False):
        self.calls = []
        self.ids = list(ids)
        self.fail = fail

    def call(self, _dest, _path, _iface, member, sig="", args=(), **_kw):
        if self.fail:
            raise DBusError("bus went away")
        self.calls.append((member, args))
        if member == "Notify":
            return (self.ids.pop(0),)
        return ()

    def members(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setattr(deliver, "_ID_FILE", tmp_path / "notify-id")
    monkeypatch.setattr(deliver, "RUN", tmp_path)
    fake = FakeBus()
    monkeypatch.setattr(deliver, "_notifier", lambda: fake)
    return fake


def replaces(args):
    """The replaces_id argument of a Notify call."""
    return args[1]


def test_state_reuses_one_bubble(bus):
    deliver.notify("Listening…")
    deliver.notify("Transcribing…")
    assert bus.members() == ["Notify", "Notify"]
    assert replaces(bus.calls[0][1]) == 0     # nothing to replace yet
    assert replaces(bus.calls[1][1]) == 11    # the first bubble's id


def test_other_categories_close_the_state_bubble_and_pop_fresh(bus):
    deliver.notify("Listening…")
    deliver.notify("Typed at the cursor", category="delivery")
    assert bus.members() == ["Notify", "CloseNotification", "Notify"]
    assert bus.calls[1][1] == (11,)
    assert replaces(bus.calls[2][1]) == 0     # a fresh popup, not a replace
    assert not deliver._ID_FILE.exists()      # and the slot is empty again


def test_the_id_outlives_the_process(bus):
    """The tray says "Listening" and `dictate` says "Transcribing" from
    a different process, so the id has to be on disk to be shared."""
    deliver.notify("Listening…")
    assert deliver._ID_FILE.read_text() == "11"


def test_a_disabled_category_says_nothing(bus, monkeypatch):
    from dictatr import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings.notify, "state", False)
    deliver.notify("Listening…")
    assert bus.calls == []


def test_a_bus_that_drops_us_loses_the_bubble_not_the_dictation(
        tmp_path, monkeypatch):
    monkeypatch.setattr(deliver, "_ID_FILE", tmp_path / "notify-id")
    monkeypatch.setattr(deliver, "_bus", FakeBus(fail=True))
    deliver.notify("Listening…")              # must not raise
    assert deliver._bus is None               # and reconnects next time
