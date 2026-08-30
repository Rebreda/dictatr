"""A small D-Bus client, in the standard library.

dictatr spoke D-Bus by spawning things: notify-send for every state
change, gdbus to close the bubble it left behind, spectacle or grim for
a screenshot. That is a process per notification, three more packages to
depend on, and three programs that may not exist -- on a desktop where
the bus itself is always there and the calls are a few hundred bytes.

This is the client those calls need and nothing more: connect, call a
method, read a reply, wait for one signal. No properties, no object
manager, no server side, no file descriptors.

Written against the wire protocol rather than PyGObject because
src/dictatr is stdlib-only on purpose -- `dictate` runs in a terminal
with no display and no GTK, and the surfaces that do have gi already
have a better client than this one. See ui/portal.py for that side.

The protocol is fixed-width little-endian values, each padded to its own
alignment, in messages of a 16-byte header plus a header-field array
plus a body. Alignment is measured from the start of the message, which
is why the writer carries a base offset instead of just concatenating.
"""

import os
import socket
import struct
import time
from collections import namedtuple

# A value that travels with its own type, for the a{sv} option dicts
# every portal and the notification spec use.
Variant = namedtuple("Variant", "sig value")

METHOD_CALL, METHOD_RETURN, ERROR, SIGNAL = 1, 2, 3, 4
NO_REPLY_EXPECTED = 1

# Header field codes (see the spec's table): the ones a client sends or
# reads. 7 (SENDER) and 9 (UNIX_FDS) arrive and are ignored.
PATH, INTERFACE, MEMBER, ERROR_NAME, REPLY_SERIAL, DESTINATION, \
    SENDER, SIGNATURE = 1, 2, 3, 4, 5, 6, 7, 8

_ALIGN = {"y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8,
          "d": 8, "s": 4, "o": 4, "g": 1, "a": 4, "(": 8, "{": 8, "v": 1}
_FIXED = {"y": "B", "b": "I", "n": "h", "q": "H", "i": "i", "u": "I",
          "x": "q", "t": "Q", "d": "d"}


class DBusError(RuntimeError):
    """An error reply, or a connection that could not be made."""


def _types(sig: str) -> list[str]:
    """Split a signature into its complete top-level types."""
    out, i = [], 0
    while i < len(sig):
        j = _end(sig, i)
        out.append(sig[i:j])
        i = j
    return out


def _end(sig: str, i: int) -> int:
    """Index just past the single complete type starting at *i*."""
    c = sig[i]
    if c == "a":
        return _end(sig, i + 1)
    if c in "({":
        close = ")" if c == "(" else "}"
        depth, j = 1, i + 1
        while depth:
            if sig[j] in "({":
                depth += 1
            elif sig[j] in ")}":
                depth -= 1
                if not depth:
                    return j + 1
            j += 1
    return i + 1


class _Writer:
    def __init__(self, base: int = 0):
        self.buf = bytearray()
        self.base = base

    def pad(self, n: int) -> None:
        while (self.base + len(self.buf)) % n:
            self.buf.append(0)

    def write(self, sig: str, value) -> None:
        c = sig[0]
        if c in _FIXED:
            self.pad(_ALIGN[c])
            self.buf += struct.pack("<" + _FIXED[c],
                                    int(value) if c != "d" else value)
        elif c in "so":
            raw = str(value).encode()
            self.pad(4)
            self.buf += struct.pack("<I", len(raw)) + raw + b"\0"
        elif c == "g":
            raw = str(value).encode()
            self.buf += bytes([len(raw)]) + raw + b"\0"
        elif c == "v":
            v = value if isinstance(value, Variant) else Variant("s", value)
            self.write("g", v.sig)
            self.write(v.sig, v.value)
        elif c == "a":
            self._array(sig[1:], value)
        elif c == "(":
            self.pad(8)
            for t, v in zip(_types(sig[1:-1]), value):
                self.write(t, v)
        else:
            raise DBusError(f"cannot marshal {sig!r}")

    def _array(self, elem: str, value) -> None:
        self.pad(4)
        at = len(self.buf)
        self.buf += b"\0\0\0\0"          # length, back-filled below
        self.pad(_ALIGN[elem[0]])        # padding is not part of the count
        start = len(self.buf)
        items = value.items() if elem[0] == "{" else value
        for item in items:
            if elem[0] == "{":
                k, t = _types(elem[1:-1])
                self.pad(8)
                self.write(k, item[0])
                self.write(t, item[1])
            else:
                self.write(elem, item)
        struct.pack_into("<I", self.buf, at, len(self.buf) - start)


class _Reader:
    def __init__(self, buf: bytes, base: int = 0):
        self.buf, self.pos, self.base = buf, 0, base

    def pad(self, n: int) -> None:
        while (self.base + self.pos) % n:
            self.pos += 1

    def read(self, sig: str):
        c = sig[0]
        if c in _FIXED:
            self.pad(_ALIGN[c])
            fmt = "<" + _FIXED[c]
            size = struct.calcsize(fmt)
            (v,) = struct.unpack_from(fmt, self.buf, self.pos)
            self.pos += size
            return bool(v) if c == "b" else v
        if c in "so":
            self.pad(4)
            (n,) = struct.unpack_from("<I", self.buf, self.pos)
            self.pos += 4
            v = self.buf[self.pos:self.pos + n].decode()
            self.pos += n + 1
            return v
        if c == "g":
            n = self.buf[self.pos]
            self.pos += 1
            v = self.buf[self.pos:self.pos + n].decode()
            self.pos += n + 1
            return v
        if c == "v":
            return self.read(self.read("g"))
        if c == "a":
            return self._array(sig[1:])
        if c == "(":
            self.pad(8)
            return tuple(self.read(t) for t in _types(sig[1:-1]))
        raise DBusError(f"cannot unmarshal {sig!r}")

    def _array(self, elem: str):
        self.pad(4)
        (n,) = struct.unpack_from("<I", self.buf, self.pos)
        self.pos += 4
        self.pad(_ALIGN[elem[0]])
        end = self.pos + n
        if elem[0] == "{":
            k, t = _types(elem[1:-1])
            out = {}
            while self.pos < end:
                self.pad(8)
                # Two statements: Python evaluates the right-hand side
                # of a subscript assignment first, which would read the
                # value off the wire before the key.
                key = self.read(k)
                out[key] = self.read(t)
            return out
        out = []
        while self.pos < end:
            out.append(self.read(elem))
        return out


def _message(kind: int, serial: int, fields: dict, sig: str, args) -> bytes:
    body = _Writer()
    for t, v in zip(_types(sig), args):
        body.write(t, v)
    flags = NO_REPLY_EXPECTED if fields.pop("no_reply", False) else 0
    head = _Writer(base=12)
    head.write("a(yv)", [(code, val) for code, val in fields.items()])
    fixed = struct.pack("<BBBBII", ord("l"), kind, flags, 1,
                        len(body.buf), serial)
    out = bytearray(fixed + head.buf)
    while len(out) % 8:
        out.append(0)
    return bytes(out + body.buf)


Message = namedtuple("Message", "kind serial fields body")


def _parse(raw: bytes) -> Message:
    kind = raw[1]
    body_len, serial = struct.unpack_from("<II", raw, 4)
    (fields_len,) = struct.unpack_from("<I", raw, 12)
    fields = dict(_Reader(raw[12:], base=12).read("a(yv)"))
    start = 16 + fields_len
    start += -start % 8
    body_raw = raw[start:start + body_len]
    sig = fields.get(SIGNATURE, "")
    # One reader for the whole body: values are laid out end to end and
    # each one's alignment is measured from where the last one stopped.
    reader = _Reader(body_raw)
    body = tuple(reader.read(t) for t in _types(sig)) if sig else ()
    return Message(kind, serial, fields, body)


class Bus:
    """One connection to the session bus."""

    def __init__(self, sock: socket.socket, unique: str):
        self.sock, self.unique = sock, unique
        self._serial = 1
        self._pending: list[Message] = []

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def send(self, dest, path, iface, member, sig="", args=(),
             no_reply=False) -> int:
        self._serial += 1
        fields = {PATH: Variant("o", path), MEMBER: Variant("s", member)}
        if iface:
            fields[INTERFACE] = Variant("s", iface)
        if dest:
            fields[DESTINATION] = Variant("s", dest)
        if sig:
            fields[SIGNATURE] = Variant("g", sig)
        if no_reply:
            fields["no_reply"] = True
        self.sock.sendall(
            _message(METHOD_CALL, self._serial, fields, sig, args))
        return self._serial

    def _recv(self, timeout: float) -> Message | None:
        self.sock.settimeout(max(timeout, 0.001))
        try:
            head = self._exactly(16)
            (fields_len,) = struct.unpack_from("<I", head, 12)
            body_len = struct.unpack_from("<I", head, 4)[0]
            rest = 16 + fields_len
            rest += -rest % 8
            raw = head + self._exactly(rest - 16 + body_len)
        except (socket.timeout, TimeoutError, OSError):
            return None
        return _parse(raw)

    def _exactly(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise OSError("bus closed")
            out += chunk
        return out

    def call(self, dest, path, iface, member, sig="", args=(),
             timeout: float = 5.0):
        """Call a method and return its reply body as a tuple."""
        serial = self.send(dest, path, iface, member, sig, args)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._recv(deadline - time.monotonic())
            if msg is None:
                continue
            if msg.fields.get(REPLY_SERIAL) != serial:
                self._pending.append(msg)   # a signal, or someone else's
                continue
            if msg.kind == ERROR:
                detail = msg.body[0] if msg.body else ""
                raise DBusError(f"{msg.fields.get(ERROR_NAME)}: {detail}")
            return msg.body
        raise DBusError(f"{member}: no reply within {timeout}s")

    def wait_signal(self, path: str, member: str, timeout: float = 60.0):
        """Body of the next matching signal, or None if it never comes.

        Checks what already arrived first: a portal can answer before the
        caller starts waiting, and that reply is sitting in _pending."""
        def matches(m):
            return (m.kind == SIGNAL and m.fields.get(MEMBER) == member
                    and m.fields.get(PATH) == path)

        for i, msg in enumerate(self._pending):
            if matches(msg):
                return self._pending.pop(i).body
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._recv(deadline - time.monotonic())
            if msg is None:
                continue
            if matches(msg):
                return msg.body
            self._pending.append(msg)
        return None

    def add_match(self, rule: str) -> None:
        self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                  "org.freedesktop.DBus", "AddMatch", "s", (rule,))


def name_has_owner(name: str, timeout: float = 3.0) -> bool:
    """Is anything answering to *name* on the session bus?

    How a surface asks "is the tray running" without caring how it was
    started or where its pidfile went."""
    bus = session()
    if bus is None:
        return False
    with bus:
        try:
            return bool(bus.call("org.freedesktop.DBus",
                                 "/org/freedesktop/DBus",
                                 "org.freedesktop.DBus", "NameHasOwner",
                                 "s", (name,), timeout=timeout)[0])
        except DBusError:
            return False


def session(timeout: float = 2.0) -> Bus | None:
    """Connect to the session bus, or None if there is not one.

    None rather than an exception: every caller here is doing something
    optional (a notification, a screenshot) and a machine with no bus
    should lose the notification, not the dictation.
    """
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    path = None
    for part in addr.split(";"):
        if part.startswith("unix:"):
            for kv in part[5:].split(","):
                k, _, v = kv.partition("=")
                if k in ("path", "abstract"):
                    path = ("\0" + v) if k == "abstract" else v
    if path is None:
        guess = f"/run/user/{os.getuid()}/bus"
        path = guess if os.path.exists(guess) else None
    if path is None:
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(path)
        # SASL: the byte of nul is the protocol's, not a formality --
        # it carries the credentials on the socket.
        sock.sendall(b"\0AUTH EXTERNAL "
                     + str(os.getuid()).encode().hex().encode() + b"\r\n")
        if not sock.recv(1024).startswith(b"OK"):
            raise DBusError("bus refused EXTERNAL auth")
        sock.sendall(b"BEGIN\r\n")
        bus = Bus(sock, "")
        bus.unique = bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                              "org.freedesktop.DBus", "Hello")[0]
        return bus
    except (OSError, DBusError):
        return None
