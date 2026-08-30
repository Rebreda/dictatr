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
# Copy a subtree of the repo into $lib, keeping its layout. The flat
# globs this replaced (src/dictatr/*.py, ui/*.py, ui/icons/*.png, and
# the icon theme spelled out by hand) silently dropped every
# subdirectory: 0.3.0 shipped without src/dictatr/backend/ at all, so
# `dictate` died on import for everyone who installed a package. Depth
# is the packager's problem, not something each new directory has to
# remember to announce.
stage_tree() {
    local rel=$1 mode=$2; shift 2
    local f
    while IFS= read -r -d '' f; do
        install -Dpm"$mode" "$f" "$lib/${f#"$repo"/}"
    done < <(find "$repo/$rel" -type f "$@" -print0)
}

# The wizard's symbolic icons ride along under ui/icons/theme, laid out
# as a theme it adds to the search path (desktop themes render these
# names unreliably); ui/kwin/*.js is loaded into KWin by the tray.
stage_tree src/dictatr 644 -name '*.py'
stage_tree ui 644 \( -name '*.py' -o -name '*.png' -o -name '*.svg' \
                     -o -name '*.js' \)
stage_tree bin 755
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
           dictate-hotkeys dictate-suggest dictate-shot; do
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
python3 "$repo/ui/shortcuts.py" --desktop |
    while IFS=$'\t' read -r name label cmd; do
        desktop "$name" "$label" "/usr/bin/$cmd"
    done

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
