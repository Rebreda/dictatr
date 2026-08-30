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

# Debian requires a changelog for every package (lintian: no-changelog).
# The rpm spec already carries one; this is the same history in the
# format dpkg expects, generated from it so there is one source of truth.
MAINTAINER=${MAINTAINER:-"Rebreda <Rebreda@users.noreply.github.com>"}
python3 "$here/spec2changelog.py" "$here/dictatr.spec" "$MAINTAINER" \
    | gzip -9n >"$stage/usr/share/doc/dictatr/changelog.gz"
chmod 644 "$stage/usr/share/doc/dictatr/changelog.gz"

mkdir -p "$stage/DEBIAN"

# Files under /etc must be registered as conffiles or dpkg overwrites
# local edits on upgrade (lintian: file-in-etc-not-marked-as-conffile).
# The spec marks the same file %config(noreplace).
cat >"$stage/DEBIAN/conffiles" <<EOF
/etc/xdg/autostart/dictatr-tray.desktop
EOF
cat >"$stage/DEBIAN/control" <<EOF
Package: dictatr
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), python3-websockets, pipewire-bin, wl-clipboard, libnotify-bin
Recommends: python3-gi, gir1.2-gtk-4.0, libgtk4-layer-shell0
Maintainer: $MAINTAINER
Homepage: https://github.com/Rebreda/dictatr
Description: Hotkey voice dictation backed by a local Lemonade Whisper server
 Press a hotkey, speak, and the transcript is typed at your cursor (or
 copied to the clipboard). Recording stops by itself when you stop
 talking; nothing leaves the machine. Includes a floating radial menu,
 a tray icon with live recording state, always-on capture into a
 listenr-compatible archive, and ask mode.
 .
 The tray offers a short setup wizard the first time it starts: it picks
 an inference engine (one it downloads and manages, an existing
 Lemonade, or any OpenAI-compatible endpoint), asks for typing and
 hotkeys, and ends with a test dictation. Rerun it with dictate-setup.
EOF

mkdir -p "$repo/dist"
# -Zxz: the default on some hosts is zstd for both members, which is
# outside what Debian policy specifies and which older dpkg cannot read
# (lintian: malformed-deb-archive).
dpkg-deb -Zxz --root-owner-group --build "$stage" \
    "$repo/dist/dictatr_${VERSION}_all.deb"
rm -rf "$stage"
echo "built dist/dictatr_${VERSION}_all.deb"
