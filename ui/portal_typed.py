#!/usr/bin/env python3
"""Type text at the cursor via the RemoteDesktop portal.

The privileged tier of the delivery ladder (src/dictatr/deliver.py):
keysym injection through xdg-desktop-portal, no uinput access needed.
Lives under ui/ because it needs PyGObject (Gio DBus); src/dictatr stays
stdlib-only and shells out to this file.

    portal_typed.py [--] TEXT...   type argv text (stdin when no args)
    portal_typed.py --check        read-only probe: portal versions only
    portal_typed.py --grant        run the permission dance deliberately
                                   (onboarding); reports persistence
    portal_typed.py --release      release every modifier, for when an
                                   interrupted run left one held down

The portal session is restored silently from a stored token when one
exists (persist_mode 2: Plasma >= 6.1.1, GNOME >= 46); without a valid
token the compositor shows a permission dialog, so deliver.py only calls
this when a token is stored. Every Start refresh replaces the token; a
Start that returns none drops it so the next dictation falls back to
the clipboard instead of popping dialogs.

Exit codes: 0 typed/granted, 1 portal refused or timed out, 2 no bus.
Every portal wait is bounded (10 s, 120 s for Start where a dialog may
be up); this process never hangs a dictation.

gi imports are lazy so the pure helpers below (token file, keysym map)
stay importable from the test suite's venv, which has no PyGObject.
"""

import argparse
import os
import sys
import time
from pathlib import Path

APP_ID = "io.github.rebreda.dictatr"
BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
RD_IFACE = "org.freedesktop.portal.RemoteDesktop"
GS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
KEYBOARD = 1              # AvailableDeviceTypes bit
CALL_TIMEOUT_S = 10
START_TIMEOUT_S = 120     # Start may be showing a dialog
KEY_DELAY_MS = 12
# Every modifier we might leave latched. The compositor owns key state,
# so anything still down when this process exits stays down for the rest
# of the session: a stuck Shift turns every mouse wheel into a sideways
# scroll, which reads as "scrolling broke" rather than "dictation broke".
MODIFIER_KEYSYMS = (
    0xffe1, 0xffe2,   # Shift_L, Shift_R
    0xffe3, 0xffe4,   # Control_L, Control_R
    0xffe9, 0xffea,   # Alt_L, Alt_R
    0xffeb, 0xffec,   # Super_L, Super_R
    0xffe7, 0xffe8,   # Meta_L, Meta_R
    0xfe03,           # ISO_Level3_Shift (AltGr)
)


# --- pure helpers (unit-tested, no gi) ---------------------------------

def token_path() -> Path:
    # Keep in sync with _portal_token() in src/dictatr/deliver.py.
    state = Path(os.environ.get("XDG_STATE_HOME")
                 or Path.home() / ".local" / "state")
    return state / "dictatr" / "portal-typing-token"


def load_token() -> str | None:
    try:
        return token_path().read_text().strip() or None
    except OSError:
        return None


def save_token(token: str) -> None:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch(mode=0o600, exist_ok=True)
    p.chmod(0o600)   # tighten a pre-existing file too
    p.write_text(token)


def drop_token() -> None:
    token_path().unlink(missing_ok=True)


SHIFT_L = 0xffe1
# Characters typed with Shift held. Letters answer for themselves on any
# Latin layout; the symbols follow the US arrangement, which is the only
# part a different layout could get wrong.
_SHIFTED_ASCII = frozenset('~!@#$%^&*()_+{}|:"<>?')


def needs_shift(ch: str) -> bool:
    return ch.isupper() or ch in _SHIFTED_ASCII


_KEYSYM_SPECIALS = {"\n": 0xff0d, "\r": 0xff0d, "\t": 0xff09}


def keysym_for(ch: str) -> int | None:
    """X11 keysym for a character: Latin-1 printables map directly,
    everything else is 0x01000000 + codepoint (the Unicode keysym rule);
    newline/tab need their control keysyms. Other controls are skipped."""
    if ch in _KEYSYM_SPECIALS:
        return _KEYSYM_SPECIALS[ch]
    cp = ord(ch)
    if 0x20 <= cp <= 0x7e or 0xa0 <= cp <= 0xff:
        return cp
    if cp < 0xa0:
        return None
    return 0x01000000 + cp


# --- portal plumbing ---------------------------------------------------

class PortalError(RuntimeError):
    pass


class RemoteDesktop:
    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name().lstrip(":").replace(".", "_")
        self._n = 0

    def _call(self, iface, method, sig, args, timeout_s=CALL_TIMEOUT_S):
        return self.bus.call_sync(
            BUS, PATH, iface, method, self.GLib.Variant(sig, args), None,
            self.Gio.DBusCallFlags.NONE, int(timeout_s * 1000), None)

    def _request(self, method, sig, args, options, timeout_s):
        """Portal request pattern: the method returns a Request object
        path and the real reply is a Response signal on it; subscribe
        before calling. A GLib timeout bounds the wait."""
        GLib, Gio = self.GLib, self.Gio
        self._n += 1
        token = f"dictatr{os.getpid()}_{self._n}"
        req = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        loop = GLib.MainLoop()
        out = {}

        def on_response(_bus, _sender, _path, _iface, _sig, params):
            out["code"], out["results"] = params.unpack()
            loop.quit()

        def resubscribe(path):
            return self.bus.signal_subscribe(
                BUS, "org.freedesktop.portal.Request", "Response", path,
                None, Gio.DBusSignalFlags.NONE, on_response)

        sub = resubscribe(req)
        opts = dict(options, handle_token=GLib.Variant("s", token))
        try:
            handle = self._call(RD_IFACE, method, sig,
                                tuple(args) + (opts,)).unpack()[0]
        except GLib.Error as e:
            self.bus.signal_unsubscribe(sub)
            raise PortalError(f"{method}: {e.message}") from e
        if handle != req:   # pre-1.0 portals mint their own request path
            self.bus.signal_unsubscribe(sub)
            sub = resubscribe(handle)
        src = GLib.timeout_add(int(timeout_s * 1000),
                               lambda: (loop.quit(), False)[1])
        loop.run()
        self.bus.signal_unsubscribe(sub)
        if "code" not in out:
            raise PortalError(f"{method}: no response within {timeout_s}s")
        GLib.source_remove(src)
        if out["code"]:
            what = "cancelled" if out["code"] == 1 else "refused"
            raise PortalError(f"{method}: {what} (response {out['code']})")
        return out["results"]

    def register(self):
        """xdg-desktop-portal >= 1.20 wants non-sandboxed apps to
        self-identify before their first session; older portals lack the
        interface, so every error is ignored."""
        try:
            self._call("org.freedesktop.host.portal.Registry", "Register",
                       "(sa{sv})", (APP_ID, {}), 3)
        except Exception:
            pass

    def open_session(self, restore_token):
        """CreateSession -> SelectDevices -> Start. Returns (session
        path, refreshed restore token or None)."""
        V = self.GLib.Variant
        self.register()
        res = self._request(
            "CreateSession", "(a{sv})", (),
            {"session_handle_token": V("s", "dictatr")}, CALL_TIMEOUT_S)
        session = res.get("session_handle")
        if not session:
            raise PortalError("CreateSession: no session handle")
        opts = {"types": V("u", KEYBOARD), "persist_mode": V("u", 2)}
        if restore_token:
            opts["restore_token"] = V("s", restore_token)
        self._request("SelectDevices", "(oa{sv})", (session,), opts,
                      CALL_TIMEOUT_S)
        res = self._request("Start", "(osa{sv})", (session, ""), {},
                            START_TIMEOUT_S)
        devices = res.get("devices")
        if devices is not None and not devices & KEYBOARD:
            raise PortalError("Start: keyboard not granted")
        return session, res.get("restore_token")

    def _key(self, session, keysym, pressed):
        self._call(RD_IFACE, "NotifyKeyboardKeysym", "(oa{sv}iu)",
                   (session, {}, keysym, 1 if pressed else 0))

    def type_text(self, session, text):
        """Type *text*, holding Shift ourselves for the characters that
        need it.

        Letting the compositor work the modifier out from the keysym
        alone does not survive contact with KWin: it presses Shift a
        keystroke late and never lifts it, so "Next up," arrives as
        "nEXT UP<" and every later character in the session is shifted
        too. With Shift already down when the keysym lands there is
        nothing for it to synthesise, and the release is ours to make.
        """
        last = None
        shifted = False
        try:
            for ch in text:
                ks = keysym_for(ch)
                if ks is None:
                    continue
                want = needs_shift(ch)
                if want != shifted:
                    self._key(session, SHIFT_L, want)
                    shifted = want
                    time.sleep(KEY_DELAY_MS / 1000)
                self._key(session, ks, True)
                last = ks
                self._key(session, ks, False)
                last = None
                time.sleep(KEY_DELAY_MS / 1000)
        finally:
            self.release_all(session, last)

    def release_all(self, session, pending=None):
        """Leave no key down. The compositor holds the state, so a key
        pressed but never released outlives this process: deliver.py kills
        the helper on its timeout, and a stuck Shift silently turns every
        later mouse wheel into a horizontal scroll. Releases are harmless
        when nothing is held, so this runs unconditionally."""
        for ks in ([pending] if pending else []) + list(MODIFIER_KEYSYMS):
            try:
                self._call(RD_IFACE, "NotifyKeyboardKeysym", "(oa{sv}iu)",
                           (session, {}, ks, 0))
            except Exception:
                pass

    def close(self, session):
        try:
            self.bus.call_sync(
                BUS, session, "org.freedesktop.portal.Session", "Close",
                None, None, self.Gio.DBusCallFlags.NONE, 3000, None)
        except Exception:
            pass


# --- modes -------------------------------------------------------------

def err(msg: str) -> None:
    print(f"portal-typed: {msg}", file=sys.stderr)


def check() -> int:
    """Read-only probe: property reads only, never a session or dialog."""
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as e:
        err(f"no session bus: {e.message}")
        return 2

    def prop(iface, name):
        try:
            return bus.call_sync(
                BUS, PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (iface, name)), None,
                Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error:
            return None

    rd = prop(RD_IFACE, "version")
    devices = prop(RD_IFACE, "AvailableDeviceTypes")
    gs = prop(GS_IFACE, "version")
    keyboard = rd is not None and (devices is None or devices & KEYBOARD)
    print("RemoteDesktop portal:  "
          + (f"v{rd}, keyboard {'available' if keyboard else 'absent'}"
             if rd is not None else "absent"))
    print("GlobalShortcuts portal: "
          + (f"v{gs}" if gs is not None else "absent"))
    print(f"typing grant token:    "
          f"{'stored' if load_token() else 'none'} ({token_path()})")
    return 0 if keyboard else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Type text at the cursor via the RemoteDesktop portal")
    ap.add_argument("--check", action="store_true",
                    help="probe portal availability (read-only, no dialog)")
    ap.add_argument("--grant", action="store_true",
                    help="run the permission dance only; may show a dialog")
    ap.add_argument("--release", action="store_true",
                    help="release every modifier key (fixes a stuck Shift "
                         "or Ctrl left by an interrupted dictation)")
    ap.add_argument("text", nargs="*", help="text to type (stdin if empty)")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    try:
        rd = RemoteDesktop()
    except Exception as e:
        err(f"no session bus: {e}")
        return 2

    stored = load_token()
    try:
        session, fresh = rd.open_session(stored)
    except PortalError as e:
        if stored:
            # A stored token that no longer restores silently would pop
            # a dialog on every dictation; forget it, fall back tiers.
            drop_token()
        err(str(e))
        return 1
    # A Start with persist_mode 2 refreshes the token each time; none
    # returned means persistence was not granted (or was revoked).
    if fresh:
        save_token(fresh)
    else:
        drop_token()

    if args.grant:
        print("persistence granted, token stored" if fresh
              else "granted for this session only (no persistence)")
        rd.close(session)
        return 0

    if args.release:
        rd.release_all(session)
        print(f"released {len(MODIFIER_KEYSYMS)} modifier keys")
        rd.close(session)
        return 0

    text = " ".join(args.text) if args.text else sys.stdin.read()
    if text:
        rd.type_text(session, text)
    rd.close(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
