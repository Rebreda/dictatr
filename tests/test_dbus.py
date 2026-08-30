"""The wire format, round-tripped.

Marshalling is the part of a hand-written D-Bus client that can be
wrong in a way nothing notices: a byte of padding in the wrong place
shifts every value after it, and the bus answers with a parse error
instead of the notification the user was expecting. Alignment is
measured from the start of the message, so the cases that matter are
the ones where a value lands at an offset that needs padding.
"""

import struct

import pytest

from dictatr.dbus import (ERROR, MEMBER, METHOD_RETURN, PATH, SIGNATURE,
                          Variant, _message, _parse, _Reader, _types,
                          _Writer)


def roundtrip(sig, values):
    w = _Writer()
    for t, v in zip(_types(sig), values):
        w.write(t, v)
    r = _Reader(bytes(w.buf))
    return tuple(r.read(t) for t in _types(sig))


@pytest.mark.parametrize("sig,values", [
    ("s", ("hello",)),
    ("u", (4294967295,)),
    ("i", (-7,)),
    ("b", (True,)),
    ("y", (200,)),
    ("t", (2 ** 63,)),
    ("d", (1.5,)),
    ("as", (["a", "bb", "ccc"],)),
    ("as", ([],)),
    ("(si)", (("x", 3),)),
    ("aay", ([[1, 2], [3]],)),
])
def test_roundtrip(sig, values):
    assert roundtrip(sig, values) == values


@pytest.mark.parametrize("sig,values,plain", [
    # What a notification actually goes out as.
    ("susssasa{sv}i",
     ("Dictate", 12, "icon", "Dictate", "body", [],
      {"urgency": Variant("y", 1)}, 2500),
     ("Dictate", 12, "icon", "Dictate", "body", [], {"urgency": 1}, 2500)),
    # What a portal takes, and what it answers with.
    ("a{sv}", ({"interactive": Variant("b", True),
                "handle_token": Variant("s", "dictatr1_1")},),
     ({"interactive": True, "handle_token": "dictatr1_1"},)),
    ("ua{sv}", (0, {"uri": Variant("s", "file:///tmp/shot.png")}),
     (0, {"uri": "file:///tmp/shot.png"})),
])
def test_variants_unwrap_on_the_way_back(sig, values, plain):
    """A variant carries its type out and is a plain value coming in:
    callers write Variant("b", True) and read True."""
    assert roundtrip(sig, values) == plain


def test_string_after_byte_is_padded():
    """A byte then a string: the string's length field must land on a
    4-byte boundary, so three bytes of padding sit between them."""
    w = _Writer()
    w.write("y", 1)
    w.write("s", "hi")
    assert bytes(w.buf) == b"\x01\0\0\0" + struct.pack("<I", 2) + b"hi\0"


def test_body_length_and_alignment():
    raw = _message(1, 7, {PATH: Variant("o", "/x"),
                          MEMBER: Variant("s", "Ping"),
                          SIGNATURE: Variant("g", "s")}, "s", ("hi",))
    assert len(raw) % 8 == struct.unpack_from("<I", raw, 4)[0] % 8
    msg = _parse(raw)
    assert msg.serial == 7
    assert msg.fields[MEMBER] == "Ping"
    assert msg.body == ("hi",)


def test_parse_reply_with_no_body():
    raw = _message(METHOD_RETURN, 3, {}, "", ())
    msg = _parse(raw)
    assert msg.kind == METHOD_RETURN and msg.body == ()


def test_parse_error_carries_its_message():
    from dictatr.dbus import ERROR_NAME
    raw = _message(ERROR, 4,
                   {ERROR_NAME: Variant("s", "org.example.Nope"),
                    SIGNATURE: Variant("g", "s")}, "s", ("no",))
    msg = _parse(raw)
    assert msg.kind == ERROR
    assert msg.fields[ERROR_NAME] == "org.example.Nope"
    assert msg.body == ("no",)


def test_parse_reads_a_multi_value_body_in_order():
    """Every value in a body shares one cursor. Reading each from its
    own reader restarts at offset 0, so the second value is parsed out
    of the first one's bytes -- which is how a portal reply of
    (code, results) came back as a struct error instead of an answer."""
    raw = _message(METHOD_RETURN, 9, {SIGNATURE: Variant("g", "ua{sv}")},
                   "ua{sv}", (0, {"uri": Variant("s", "file:///s.png")}))
    assert _parse(raw).body == (0, {"uri": "file:///s.png"})


def test_parse_reads_an_empty_results_dict():
    """The shape of a cancelled portal response."""
    raw = _message(METHOD_RETURN, 9, {SIGNATURE: Variant("g", "ua{sv}")},
                   "ua{sv}", (1, {}))
    assert _parse(raw).body == (1, {})
