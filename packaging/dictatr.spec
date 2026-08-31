Name:           dictatr
Version:        0.5.1
Release:        1%{?dist}
Summary:        Hotkey voice dictation for Wayland desktops
License:        MIT
URL:            https://github.com/Rebreda/dictatr
# A full URL, as the guidelines require: COPR and Fedora both fetch the
# source rather than being handed one.
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

# stage.sh generates the desktop entries with python3 and compresses the
# man page; everything else it needs is in the minimal buildroot.
BuildRequires:  python3
BuildRequires:  gzip

Requires:       python3 >= 3.11
Requires:       python3-websockets
# File dependencies on the commands actually run, rather than naming the
# library packages that happen to carry them today (rpmlint:
# explicit-lib-dependency).
Requires:       /usr/bin/pw-record
# The clipboard is the one desktop service with no bus API: a Wayland
# selection is owned by a live client, and `dictate` exits, so something
# has to stay behind holding the text. That is what wl-copy forks to do.
# Notifications and screenshots go over the session bus instead (see
# src/dictatr/dbus.py), so libnotify and a screenshot tool are gone.
Requires:       /usr/bin/wl-copy
Recommends:     gtk4
Recommends:     python3-gobject
Recommends:     gtk4-layer-shell

%description
Press a hotkey, speak, and the transcript is typed at your cursor (or
copied to the clipboard). Recording stops by itself when you stop
talking. Backed by a local Lemonade inference server; nothing leaves
the machine. Includes a floating radial menu, a tray icon with live
recording state, always-on capture into a listenr-compatible archive,
and ask mode (local LLM answers with recall over past dictations).

Setup runs itself: the tray offers a short wizard the first time it
starts, which picks an inference engine (one it downloads and
manages, an existing Lemonade, or any OpenAI-compatible endpoint), asks
for typing permission and hotkeys, and ends with a test dictation. Run
it again any time with `dictate-setup`.

%prep
%setup -q

%install
bash packaging/stage.sh %{buildroot}

%files
%license LICENSE
/usr/lib/dictatr
/usr/bin/dictate
/usr/bin/dictate-menu
/usr/bin/dictate-tray
/usr/bin/dictate-chat
/usr/bin/dictate-setup
/usr/bin/dictate-hotkeys
/usr/bin/dictate-suggest
/usr/bin/dictate-shot
/usr/share/applications/dictate.desktop
/usr/share/applications/dictate-menu.desktop
/usr/share/applications/dictate-cancel.desktop
/usr/share/applications/dictate-listen.desktop
/usr/share/applications/dictate-chat.desktop
/usr/share/applications/dictate-suggest.desktop
/usr/share/applications/dictate-shot.desktop
/usr/share/applications/io.github.rebreda.dictatr.desktop
/usr/share/applications/dictatr-setup.desktop
%config(noreplace) /etc/xdg/autostart/dictatr-tray.desktop
/usr/lib/systemd/user/dictatr-listen.service
/usr/lib/systemd/user/dictatr-gc.service
/usr/lib/systemd/user/dictatr-gc.timer
/usr/share/icons/hicolor/scalable/apps/dictatr.svg
%{_mandir}/man1/dictate*.1*

%changelog
* Sun Aug 30 2026 Rebreda - 0.5.1-1
- The menu, the selection actions and the settings are one surface now.
  A single resident process draws all three, so opening the settings
  from the menu is a movement inside the ring you are already looking
  at rather than a window from a different program appearing beside it
- The settings are bubbles rather than a form: speaking answers aloud,
  what Ask may read, the models, how long a pause ends a segment,
  notifications, the archive. There is no Save button -- a setting is
  written the moment you press it, and every dictatr process reads it
  on its next use
- The menu can be used with a mouse again: bubbles light up under the
  pointer and give under a press, the middle backs out of a submenu or
  closes, a click on the desktop behind it dismisses, and it appears
  where the pointer is however long ago it was last opened
- It also arrives and leaves rather than blinking: the bubbles spiral
  out of the middle when it opens and back into it when it closes, and
  catching one mid-flight turns it around instead of starting over
- Opening a submenu no longer leaves the menu you came from sitting on
  top of it at four times its size. The level you have flown through
  fades as you pass it and leaves one faint trace of where you came from
- dictate file says why a file could not be transcribed instead of
  failing silently
- A malformed number in the config is reported once and the default is
  used, rather than raising from whichever line happened to read it next
- The launchers no longer exit before launching anything on distributions
  that keep gtk4-layer-shell in /usr/lib64, which is what "the hotkey
  does nothing" looked like from the outside
- The voice chat takes the keyboard when it opens, so its field can be
  typed into without clicking it first
- Dictation no longer types a revision while the hotkey chord is still
  held down

* Sun Aug 30 2026 Rebreda - 0.5.0-1
- Recordings move to ~/.local/share/dictatr/archive, out of listenr's
  directory. An existing ~/.listenr/dictation is left where it is until
  tools/archive-migrate --go moves it, and an explicit archive= or
  DICTATE_ARCHIVE still wins; dictatr says the script exists once per
  command until it is run
- Ask about a screen region (Ctrl+Alt+G): drag out a region, crop, mark
  up or redact it, and the picture goes to the vision model with the
  question you then speak or type
- Selection actions on a ring (Ctrl+Alt+S): what dictatr can do with the
  highlighted text, on screen at once and replaced by the model's
  shortlist for that text a moment later. The model only ever picks from
  the catalogue, so every action is one you have seen
- A hotkey for the voice chat (Ctrl+Alt+Q), which now takes typed
  questions as well as spoken ones, and offers what to do next with an
  answer instead of ending at it
- Conversations are kept: each is written as it happens to
  chats/YYYY-MM-DD/chat_<uid>.jsonl in the archive, assets beside it
- Pointer gestures on KDE: shake the pointer, or draw a circle, to run
  any of the shortcuts. Off unless mapped, except a vertical shake,
  which opens the voice chat. gesture_shake_v = "" turns it off
- The chat can show its working (chat_details): the context it read,
  what it recalled, every tool it called and what came back
- dictatr knows which application has focus, so a dictation records the
  app it was spoken in and recall prefers what you said in the app in
  front of you. The class only, never the window title
- Settings are read when they are used, so a value the settings window
  or the wizard writes reaches the tray, the chat and the listener
  without restarting any of them
- Diagnostics are named topics now: debug = "gesture" logs every trace
  the compositor hands over with the measurements behind the verdict
- Notifications from the menu and the tray respect the categories in
  Settings, which they previously bypassed
* Sat Aug 29 2026 Rebreda - 0.4.0-1
- Type as you speak: words land at the cursor while the utterance is
  still being transcribed, erasing back to the part that still matches
  when the engine revises a word (live_typing = false to insert once)
- The tray closes its shortcut session on exit, so restarting it no
  longer races its own teardown and leaves the hotkeys bound to nothing
- The setup wizard hands the live shortcut session back to the tray
  instead of leaving the keys with the session it is about to close
- tools/typeprobe types into its own window and reports what arrived,
  so keysym injection can be checked without an editor or a screenshot
* Sat Aug 29 2026 Rebreda - 0.3.0-1
- Drop ydotool: typing is the desktop portal, then the clipboard. No
  uinput device, no udev rule, no root step in the install
- The setup wizard is a radial overlay now, the same surface as the
  menu, rather than a dialog
- Fix global hotkeys, which were bound to nothing on KDE: portal work
  runs on a private connection so Register succeeds, and empty triggers
  are repaired through kglobalaccel after retiring legacy entries
- Portal typing holds Shift itself instead of leaving the compositor to
  synthesise it, which used to latch Shift for the rest of the session
- Typing waits for the hotkey chord to be released, so injected keysyms
  are not resolved against modifiers that are still physically down
- DICTATE_TYPE_CMD overrides the typing command, for test harnesses

* Sat Aug 29 2026 Rebreda - 0.2.0-1
- Setup wizard: engine, typing permission, hotkeys, test dictation
- Backend providers: bundled lemond, detected system server, or custom
  OpenAI-compatible endpoints
- Ship a desktop file named for the app id, without which the desktop
  portal refused global shortcuts and typing grants
- No post-install shell instructions; the wizard does that work

* Wed Aug 26 2026 Rebreda - 0.1.0-1
- Initial package
