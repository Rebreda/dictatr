"""Every setting, exercised the four ways one can be reached.

The Setting descriptor resolves environment first, then the config file,
then its default, on every read. That is three code paths and a type
coercion per setting, and there are 36 of them; this walks the registry
rather than naming them, so a setting added tomorrow is covered without
anyone remembering to come back here.
"""

import pytest

from dictatr import settings as S
from dictatr.settings import REGISTRY, Setting, Settings


def groups(s):
    """Every object on Settings that carries Setting descriptors."""
    yield s
    for name in vars(s):
        member = getattr(s, name)
        if any(isinstance(v, Setting) for v in type(member).__dict__.values()):
            yield member


def reachable():
    """key -> (owner, attribute), for every setting you can actually read."""
    found = {}
    s = Settings()
    for owner in groups(s):
        for attr, value in type(owner).__dict__.items():
            if isinstance(value, Setting):
                found[value.key] = (owner, attr)
    return found


REACHABLE = reachable()


def test_every_registered_key_is_reachable():
    """A key nothing reads is a key the writers will happily set and no
    surface will ever honour -- the exact silence the registry exists to
    break."""
    assert set(REGISTRY) == set(REACHABLE), (
        f"registered but unreadable: {sorted(set(REGISTRY) - set(REACHABLE))}; "
        f"readable but unregistered: {sorted(set(REACHABLE) - set(REGISTRY))}")


def sample(kind):
    """A value of *kind* that is distinguishable from any default here."""
    return {bool: "true", int: "7", float: "0.5", str: "sentinel"}[kind]


def expected(kind):
    return {bool: True, int: 7, float: 0.5, str: "sentinel"}[kind]


@pytest.mark.parametrize("key", sorted(REGISTRY), ids=str)
def test_default_is_what_the_registry_declares(key, monkeypatch):
    owner, attr = REACHABLE[key]
    setting = REGISTRY[key]
    monkeypatch.delenv(setting.env, raising=False)
    monkeypatch.setattr(S, "_cfg", {}, raising=False)
    assert getattr(owner, attr) == setting.coerce(setting.default)


@pytest.mark.parametrize("key", sorted(REGISTRY), ids=str)
def test_environment_overrides_and_coerces(key, monkeypatch):
    owner, attr = REACHABLE[key]
    setting = REGISTRY[key]
    monkeypatch.setattr(S, "_cfg", {}, raising=False)
    monkeypatch.setenv(setting.env, sample(setting.kind))
    assert getattr(owner, attr) == expected(setting.kind)


@pytest.mark.parametrize("key", sorted(REGISTRY), ids=str)
def test_config_file_is_read_and_coerced(key, monkeypatch):
    owner, attr = REACHABLE[key]
    setting = REGISTRY[key]
    monkeypatch.delenv(setting.env, raising=False)
    monkeypatch.setattr(S, "_cfg", {key: sample(setting.kind)}, raising=False)
    assert getattr(owner, attr) == expected(setting.kind)


@pytest.mark.parametrize("key", sorted(REGISTRY), ids=str)
def test_environment_beats_the_config_file(key, monkeypatch):
    """The override of last resort has to win everywhere, or a shell that
    sets one is lying to whoever set it."""
    owner, attr = REACHABLE[key]
    setting = REGISTRY[key]
    monkeypatch.setattr(S, "_cfg", {key: "not-this"}, raising=False)
    monkeypatch.setenv(setting.env, sample(setting.kind))
    assert getattr(owner, attr) == expected(setting.kind)


@pytest.mark.parametrize("key", sorted(REGISTRY), ids=str)
def test_empty_falls_back_to_the_default(key, monkeypatch):
    """An unset variable and one set to nothing are the same intent, and
    a blank string reaching a float() is a crash on startup."""
    owner, attr = REACHABLE[key]
    setting = REGISTRY[key]
    monkeypatch.setattr(S, "_cfg", {}, raising=False)
    monkeypatch.setenv(setting.env, "")
    assert getattr(owner, attr) == setting.coerce(setting.default)


@pytest.mark.parametrize("key", sorted(REGISTRY), ids=str)
def test_reading_a_setting_never_raises(key, monkeypatch):
    """Whatever is in the file, a surface has to start. A malformed value
    is a wrong value, not a traceback on a user's desktop."""
    owner, attr = REACHABLE[key]
    setting = REGISTRY[key]
    monkeypatch.delenv(setting.env, raising=False)
    monkeypatch.setattr(S, "_cfg", {key: "not-a-number"}, raising=False)
    try:
        getattr(owner, attr)
    except Exception as e:                       # noqa: BLE001
        pytest.fail(f"{key} raised {type(e).__name__}: {e}")
