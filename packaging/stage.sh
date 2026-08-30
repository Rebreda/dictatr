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
# One-time maintenance scripts. Not surfaces and not on $PATH: the only
# one so far moves an archive out of ~/.listenr, which a fresh install
# never has to do and an upgraded one does exactly once.
stage_tree tools 755

# Anything carrying a #! is executed, not imported, and shipping it 644
# is an rpmlint error (non-executable-script). Decided by the file
# itself so a new surface does not have to be added to a list.
# `case` rather than `[ ... ] && chmod`: under set -e a loop whose last
# file has no shebang would end non-zero and kill the build.
find "$lib/ui" "$lib/src" -name '*.py' -type f | while IFS= read -r f; do
    case "$(head -c2 "$f")" in "#!") chmod 755 "$f" ;; esac
done
# ...and the reverse under bin/: a helper meant to be sourced has no
# shebang, and shipping it executable is a lintian warning
# (executable-not-elf-or-script).
find "$lib/bin" -type f | while IFS= read -r f; do
    case "$(head -c2 "$f")" in "#!") ;; *) chmod 644 "$f" ;; esac
done
install -Dpm644 "$repo/docs/assets/logo.svg" "$lib/docs/assets/logo.svg"
install -Dpm644 "$repo/docs/assets/logo.png" "$lib/docs/assets/logo.png"

# The inference engine is NOT packaged. It is a prebuilt x86-64 binary,
# which both Debian and Fedora forbid bundling, and which made an
# "Architecture: all" package ship an ELF (lintian and rpmlint both
# error on it). dictatr fetches the pinned release to
# ~/.local/share/dictatr/lemond on first use of the managed backend,
# checksummed, and the model it needs is a gigabyte over the network
# anyway -- so vendoring 13 MB bought nothing and cost the package its
# architecture. A distro that packages lemond itself can drop it at
# /usr/lib/dictatr/lemond/lemond and dictatr will prefer it.

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
# --- man pages -------------------------------------------------------
# One real page; the other launchers are .so stubs pointing at it, which
# is the standard way to document a suite of related commands without
# maintaining eight copies (lintian/rpmlint: no-manual-page).
man=$DESTDIR/usr/share/man/man1
install -dm755 "$man"
gzip -9nc "$here/dictate.1" >"$man/dictate.1.gz"
for cmd in dictate-menu dictate-tray dictate-chat dictate-setup \
           dictate-hotkeys dictate-suggest dictate-shot; do
    # The .so target names the uncompressed page; man resolves it to the
    # .gz itself. Pages must ship compressed (lintian:
    # uncompressed-manual-page); rpm would have compressed them anyway.
    printf '.so man1/dictate.1\n' | gzip -9nc >"$man/$cmd.1.gz"
done
chmod 644 "$man"/*.gz

# --- icon ------------------------------------------------------------
install -Dpm644 "$repo/docs/assets/logo.svg" \
    "$DESTDIR/usr/share/icons/hicolor/scalable/apps/dictatr.svg"
