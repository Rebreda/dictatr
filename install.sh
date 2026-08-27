#!/usr/bin/env bash
# Symlink dictatr into ~/.local/bin, install launchers, bind hotkeys.
set -eu

here=$(dirname "$(readlink -f "$0")")
mkdir -p ~/.local/bin ~/.local/share/applications

command -v uv >/dev/null || { echo "uv is required (https://docs.astral.sh/uv/)"; exit 1; }
(cd "$here" && uv sync)

ln -sf "$here/bin/dictate" ~/.local/bin/dictate
ln -sf "$here/bin/dictate-menu" ~/.local/bin/dictate-menu
ln -sf "$here/bin/dictate-tray" ~/.local/bin/dictate-tray
ln -sf "$here/bin/dictate-chat" ~/.local/bin/dictate-chat

# Tray icon: autostart at login, and start it now (single-instance safe).
mkdir -p ~/.config/autostart
cat >~/.config/autostart/dictatr-tray.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Dictate tray
Exec=$HOME/.local/bin/dictate-tray
Icon=$here/docs/logo.png
X-KDE-StartupNotify=false
EOF
nohup "$HOME/.local/bin/dictate-tray" >/dev/null 2>&1 &

for name in dictate dictate-menu dictate-cancel dictate-listen; do
    case $name in
    dictate) label="Dictate (toggle)" exec="$HOME/.local/bin/dictate type" ;;
    dictate-menu) label="Dictate menu" exec="$HOME/.local/bin/dictate-menu" ;;
    dictate-cancel) label="Dictate cancel" exec="$HOME/.local/bin/dictate cancel" ;;
    dictate-listen) label="Dictate always-on (toggle)" exec="$HOME/.local/bin/dictate listen --toggle" ;;
    esac
    cat >~/.local/share/applications/$name.desktop <<EOF
[Desktop Entry]
Type=Application
Name=$label
Exec=$exec
Icon=$here/docs/logo.png
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
kwriteconfig6 --file kglobalshortcutsrc --group services \
    --group dictate-listen.desktop --key _launch "Ctrl+Alt+A"

# Systemd user units for always-on capture + daily archive gc. Installed
# but never enabled here: an always-hot mic must be an explicit choice.
mkdir -p ~/.config/systemd/user
for unit in dictatr-listen.service dictatr-gc.service dictatr-gc.timer; do
    sed "s|@REPO@|$here|" "$here/systemd/$unit" >~/.config/systemd/user/$unit
done
command -v systemctl >/dev/null && systemctl --user daemon-reload || true

echo "Installed. Shortcuts load at next login, or assign them now in"
echo "System Settings -> Shortcuts (search \"Dictate\")."
echo
echo "Optional — always-on capture (archives everything you say; opt-in):"
echo "  systemctl --user enable --now dictatr-listen"
echo "  systemctl --user enable --now dictatr-gc.timer   # daily junk sweep"
