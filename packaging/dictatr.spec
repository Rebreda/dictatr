Name:           dictatr
Version:        0.1.0
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
Recommends:     ydotool

%description
Press a hotkey, speak, and the transcript is typed at your cursor (or
copied to the clipboard). Recording stops by itself when you stop
talking. Backed by a local Lemonade inference server; nothing leaves
the machine. Includes a floating radial menu, a tray icon with live
recording state, always-on capture into a listenr-compatible archive,
and ask mode (local LLM answers with recall over past dictations).

After installing: run dictate-hotkeys once (KDE) to bind the default
shortcuts, and enable rootless typing with
`systemctl --user enable --now dictatr-ydotoold`.

%prep
%setup -q

%install
bash packaging/stage.sh %{buildroot}

%post
# Apply the uinput uaccess rule without a reboot (best effort).
udevadm control --reload 2>/dev/null || :
udevadm trigger --name-match=uinput 2>/dev/null || :

%files
%license LICENSE
/usr/lib/dictatr
/usr/bin/dictate
/usr/bin/dictate-menu
/usr/bin/dictate-tray
/usr/bin/dictate-chat
/usr/bin/dictate-hotkeys
/usr/share/applications/dictate.desktop
/usr/share/applications/dictate-menu.desktop
/usr/share/applications/dictate-cancel.desktop
/usr/share/applications/dictate-listen.desktop
%config(noreplace) /etc/xdg/autostart/dictatr-tray.desktop
/usr/lib/systemd/user/dictatr-listen.service
/usr/lib/systemd/user/dictatr-gc.service
/usr/lib/systemd/user/dictatr-gc.timer
/usr/lib/systemd/user/dictatr-ydotoold.service
/usr/lib/udev/rules.d/70-dictatr-uinput.rules
/usr/share/icons/hicolor/scalable/apps/dictatr.svg

%changelog
* Wed Aug 26 2026 Rebreda - 0.1.0-1
- Initial package
