Name:           dictatr
Version:        0.4.0
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
Requires:       /usr/bin/wl-copy
Requires:       /usr/bin/notify-send
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
