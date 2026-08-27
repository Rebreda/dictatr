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
for f in "$repo"/ui/icons/*; do
    install -Dpm644 "$f" "$lib/ui/icons/$(basename "$f")"
done
for f in "$repo"/bin/*; do
    install -Dpm755 "$f" "$lib/bin/$(basename "$f")"
done
install -Dpm644 "$repo/docs/logo.svg" "$lib/docs/logo.svg"
install -Dpm644 "$repo/docs/logo.png" "$lib/docs/logo.png"

# --- /usr/bin -------------------------------------------------------
install -dm755 "$DESTDIR/usr/bin"
for cmd in dictate dictate-menu dictate-tray dictate-chat dictate-hotkeys; do
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
install -pm644 "$repo/systemd/dictatr-ydotoold.service" "$units/"

# --- udev: user access to uinput (rootless ydotoold) ----------------
install -Dpm644 "$repo/packaging/70-dictatr-uinput.rules" \
    "$DESTDIR/usr/lib/udev/rules.d/70-dictatr-uinput.rules"

# --- icon ------------------------------------------------------------
install -Dpm644 "$repo/docs/logo.svg" \
    "$DESTDIR/usr/share/icons/hicolor/scalable/apps/dictatr.svg"
