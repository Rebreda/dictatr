#!/usr/bin/env bash
# Symlink dictatr into ~/.local/bin, install launchers, bind hotkeys.
set -eu

here=$(dirname "$(readlink -f "$0")")
mkdir -p ~/.local/bin ~/.local/share/applications

command -v uv >/dev/null || { echo "uv is required (https://docs.astral.sh/uv/)"; exit 1; }
(cd "$here" && uv sync)

ln -sf "$here/bin/dictate" ~/.local/bin/dictate
ln -sf "$here/bin/dictate-menu" ~/.local/bin/dictate-menu

for name in dictate dictate-menu dictate-cancel; do
    case $name in
    dictate) label="Dictate (toggle)" exec="$HOME/.local/bin/dictate type" ;;
    dictate-menu) label="Dictate menu" exec="$HOME/.local/bin/dictate-menu" ;;
    dictate-cancel) label="Dictate cancel" exec="$HOME/.local/bin/dictate cancel" ;;
    esac
    cat >~/.local/share/applications/$name.desktop <<EOF
[Desktop Entry]
Type=Application
Name=$label
Exec=$exec
Icon=audio-input-microphone
NoDisplay=true
StartupNotify=false
EOF
done

kwriteconfig6 --file kglobalshortcutsrc --group services \
    --group dictate.desktop --key _launch "Ctrl+Alt+D"
kwriteconfig6 --file kglobalshortcutsrc --group services \
    --group dictate-menu.desktop --key _launch "Ctrl+Alt+Space"
kwriteconfig6 --file kglobalshortcutsrc --group services \
    --group dictate-cancel.desktop --key _launch "Ctrl+Alt+C"

echo "Installed. Shortcuts load at next login, or assign them now in"
echo "System Settings -> Shortcuts (search \"Dictate\")."
