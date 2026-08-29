#!/usr/bin/env bash
# Build the Debian/Ubuntu package into dist/. Plain dpkg-deb over the
# staged tree from stage.sh; no debhelper source-package ceremony.
#   VERSION=1.2.3 packaging/build-deb.sh   (defaults to pyproject version)
set -eu
here=$(dirname "$(readlink -f "$0")")
repo=$(dirname "$here")

VERSION=${VERSION:-$(python3 -c "
import tomllib
print(tomllib.load(open('$repo/pyproject.toml','rb'))['project']['version'])")}

stage=$repo/dist/deb-root
rm -rf "$stage"
bash "$here/stage.sh" "$stage"

install -Dm644 "$repo/LICENSE" "$stage/usr/share/doc/dictatr/copyright"

mkdir -p "$stage/DEBIAN"
cat >"$stage/DEBIAN/control" <<EOF
Package: dictatr
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), python3-websockets, pipewire-bin, wl-clipboard, libnotify-bin
Recommends: python3-gi, gir1.2-gtk-4.0, libgtk4-layer-shell0, ydotool
Maintainer: Rebreda <Rebreda@users.noreply.github.com>
Homepage: https://github.com/Rebreda/dictatr
Description: Hotkey voice dictation backed by a local Lemonade Whisper server
 Press a hotkey, speak, and the transcript is typed at your cursor (or
 copied to the clipboard). Recording stops by itself when you stop
 talking; nothing leaves the machine. Includes a floating radial menu,
 a tray icon with live recording state, always-on capture into a
 listenr-compatible archive, and ask mode.
 .
 The tray offers a short setup wizard the first time it starts: it picks
 an inference engine (its own bundled one, an existing Lemonade, or any
 OpenAI-compatible endpoint), asks the desktop for typing permission and
 hotkeys, and ends with a test dictation. Rerun it with dictate-setup.
EOF

cat >"$stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
udevadm control --reload 2>/dev/null || :
udevadm trigger --name-match=uinput 2>/dev/null || :
exit 0
EOF
chmod 755 "$stage/DEBIAN/postinst"

mkdir -p "$repo/dist"
dpkg-deb --root-owner-group --build "$stage" \
    "$repo/dist/dictatr_${VERSION}_all.deb"
rm -rf "$stage"
echo "built dist/dictatr_${VERSION}_all.deb"
