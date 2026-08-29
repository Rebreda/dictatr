#!/usr/bin/env bash
# Stage the system install tree into $1 (DESTDIR). Single source of
# truth for the rpm spec and the deb builder: everything a package
# installs is decided here.
#
# Layout: the whole app tree lives under /usr/lib/dictatr (bin, src, ui)
# so the repo-relative paths the launchers use keep working; /usr/bin
# gets symlinks. The dictate shim detects the missing .venv and runs
# `python3 -m dictatr` against the system python3-websockets.
set -eu

DESTDIR=${1:?usage: stage.sh DESTDIR}
here=$(dirname "$(readlink -f "$0")")
repo=$(dirname "$here")
lib=$DESTDIR/usr/lib/dictatr

# --- app tree -------------------------------------------------------
for f in "$repo"/src/dictatr/*.py; do
    install -Dpm644 "$f" "$lib/src/dictatr/$(basename "$f")"
done
for f in "$repo"/ui/*.py; do
    install -Dpm644 "$f" "$lib/ui/$(basename "$f")"
done
for f in "$repo"/ui/icons/*.png; do
    install -Dpm644 "$f" "$lib/ui/icons/$(basename "$f")"
done
# the wizard's own symbolic set, laid out as an icon theme it can add to
# the search path (desktop themes render these names unreliably)
for f in "$repo"/ui/icons/theme/hicolor/scalable/apps/*.svg; do
    install -Dpm644 "$f" \
        "$lib/ui/icons/theme/hicolor/scalable/apps/$(basename "$f")"
done
for f in "$repo"/bin/*; do
    install -Dpm755 "$f" "$lib/bin/$(basename "$f")"
done
install -Dpm644 "$repo/docs/assets/logo.svg" "$lib/docs/assets/logo.svg"
install -Dpm644 "$repo/docs/assets/logo.png" "$lib/docs/assets/logo.png"

# --- managed lemond (optional vendoring) ----------------------------
# DICTATR_LEMOND_TARBALL: the pinned embeddable tarball (version and
# sha256 in packaging/lemond-version.env; CI downloads and verifies).
# Without it the dir still exists with a README placeholder so package
# file lists are identical either way.
install -dm755 "$lib/lemond"
if [ -n "${DICTATR_LEMOND_TARBALL:-}" ]; then
    tar -xzf "$DICTATR_LEMOND_TARBALL" -C "$lib/lemond" \
        --strip-components=1
    chmod 755 "$lib/lemond/lemond"
    if [ -f "$lib/lemond/lemonade" ]; then
        chmod 755 "$lib/lemond/lemonade"
    fi
else
    cat >"$lib/lemond/README" <<EOF
No vendored lemond in this build. dictatr downloads the pinned release
(see packaging/lemond-version.env) to ~/.local/share/dictatr/lemond on
first use of the managed backend.
EOF
fi

# --- /usr/bin -------------------------------------------------------
install -dm755 "$DESTDIR/usr/bin"
for cmd in dictate dictate-menu dictate-tray dictate-chat dictate-setup \
           dictate-hotkeys; do
    ln -sf ../lib/dictatr/bin/$cmd "$DESTDIR/usr/bin/$cmd"
done

# --- desktop entries (hotkey launchers; names match dictate-hotkeys) -
apps=$DESTDIR/usr/share/applications
install -dm755 "$apps"
desktop() {
    cat >"$apps/$1.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Exec=$3
Icon=dictatr
NoDisplay=true
StartupNotify=false
EOF
}
desktop dictate        "Dictate (toggle)"           "/usr/bin/dictate type"
desktop dictate-menu   "Dictate menu"               "/usr/bin/dictate-menu"
desktop dictate-cancel "Dictate cancel"             "/usr/bin/dictate cancel"
desktop dictate-listen "Dictate always-on (toggle)" "/usr/bin/dictate listen --toggle"

# The portal will not hand out global shortcuts (or anything else) to an
# app whose id has no desktop file behind it: Register fails with "App
# info not found" and every later portal call is refused. So this entry
# is named for APP_ID in ui/tray.py, not for its command.
cat >"$apps/io.github.rebreda.dictatr.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=dictatr
Comment=Hotkey voice dictation
Exec=/usr/bin/dictate-menu
Icon=dictatr
Categories=Utility;AudioVideo;
StartupNotify=false
EOF

# The wizard is the other entry worth finding in the launcher.
cat >"$apps/dictatr-setup.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Set up dictatr
Comment=Choose an inference engine, allow typing, bind hotkeys
Exec=/usr/bin/dictate-setup
Icon=dictatr
Categories=Utility;Settings;
StartupNotify=true
EOF

# tray autostart for every desktop session
install -dm755 "$DESTDIR/etc/xdg/autostart"
cat >"$DESTDIR/etc/xdg/autostart/dictatr-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Dictate tray
Exec=/usr/bin/dictate-tray
Icon=dictatr
X-KDE-StartupNotify=false
EOF

# --- systemd user units ---------------------------------------------
units=$DESTDIR/usr/lib/systemd/user
install -dm755 "$units"
for unit in dictatr-listen.service dictatr-gc.service dictatr-gc.timer; do
    sed 's|@REPO@/.venv/bin/dictatr|/usr/bin/dictate|' \
        "$repo/systemd/$unit" >"$units/$unit"
done
# --- icon ------------------------------------------------------------
install -Dpm644 "$repo/docs/assets/logo.svg" \
    "$DESTDIR/usr/share/icons/hicolor/scalable/apps/dictatr.svg"
