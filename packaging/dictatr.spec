# The optionally vendored lemond (/usr/lib/dictatr/lemond, an x64 ELF
# fetched by CI per packaging/lemond-version.env) rides inside this
# otherwise-noarch package; keep noarch and silence the arch check.
%global _binaries_in_noarch_packages_terminate_build 0

Name:           dictatr
Version:        0.2.0
Release:        1%{?dist}
Summary:        Hotkey voice dictation for Linux desktops, backed by a local Lemonade Whisper server
License:        MIT
URL:            https://github.com/Rebreda/dictatr
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.11
Requires:       python3-websockets
Requires:       pipewire-utils
Requires:       wl-clipboard
Requires:       libnotify
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
starts, which picks an inference engine (its own bundled one, an
existing Lemonade, or any OpenAI-compatible endpoint), asks the desktop
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
/usr/share/applications/dictate.desktop
/usr/share/applications/dictate-menu.desktop
/usr/share/applications/dictate-cancel.desktop
/usr/share/applications/dictate-listen.desktop
/usr/share/applications/io.github.rebreda.dictatr.desktop
/usr/share/applications/dictatr-setup.desktop
%config(noreplace) /etc/xdg/autostart/dictatr-tray.desktop
/usr/lib/systemd/user/dictatr-listen.service
/usr/lib/systemd/user/dictatr-gc.service
/usr/lib/systemd/user/dictatr-gc.timer
/usr/share/icons/hicolor/scalable/apps/dictatr.svg

%changelog
* Sat Aug 29 2026 Rebreda - 0.2.0-1
- Setup wizard: engine, typing permission, hotkeys, test dictation
- Backend providers: bundled lemond, detected system server, or custom
  OpenAI-compatible endpoints
- Ship a desktop file named for the app id, without which the desktop
  portal refused global shortcuts and typing grants
- No post-install shell instructions; the wizard does that work

* Wed Aug 26 2026 Rebreda - 0.1.0-1
- Initial package
