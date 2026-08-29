#!/usr/bin/env bash
# Symlink dictatr into ~/.local/bin, install launchers, start the tray.
# Hotkeys, typing permission and the inference engine are the setup
# wizard's job: the tray offers it on its first ever start.
set -eu

here=$(dirname "$(readlink -f "$0")")
mkdir -p ~/.local/bin ~/.local/share/applications

command -v uv >/dev/null || { echo "uv is required (https://docs.astral.sh/uv/)"; exit 1; }
(cd "$here" && uv sync)

ln -sf "$here/bin/dictate" ~/.local/bin/dictate
ln -sf "$here/bin/dictate-menu" ~/.local/bin/dictate-menu
ln -sf "$here/bin/dictate-tray" ~/.local/bin/dictate-tray
ln -sf "$here/bin/dictate-chat" ~/.local/bin/dictate-chat
ln -sf "$here/bin/dictate-setup" ~/.local/bin/dictate-setup
ln -sf "$here/bin/dictate-hotkeys" ~/.local/bin/dictate-hotkeys

# Tray icon: autostart at login, and start it now (single-instance safe).
mkdir -p ~/.config/autostart
cat >~/.config/autostart/dictatr-tray.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Dictate tray
Exec=$HOME/.local/bin/dictate-tray
Icon=$here/docs/assets/logo.png
X-KDE-StartupNotify=false
EOF
nohup "$HOME/.local/bin/dictate-tray" >/dev/null 2>&1 &

# The desktop portal refuses every request from an app id it cannot find
# a desktop file for ("App info not found"), which silently kills global
# shortcuts. This entry is named for APP_ID in ui/tray.py.
cat >~/.local/share/applications/io.github.rebreda.dictatr.desktop <<EOF
[Desktop Entry]
Type=Application
Name=dictatr
Comment=Hotkey voice dictation
Exec=$HOME/.local/bin/dictate-menu
Icon=$here/docs/assets/logo.png
Categories=Utility;AudioVideo;
StartupNotify=false
EOF
cat >~/.local/share/applications/dictatr-setup.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Set up dictatr
Comment=Choose an inference engine, allow typing, bind hotkeys
Exec=$HOME/.local/bin/dictate-setup
Icon=$here/docs/assets/logo.png
Categories=Utility;Settings;
StartupNotify=true
EOF
command -v update-desktop-database >/dev/null && \
    update-desktop-database ~/.local/share/applications 2>/dev/null || true

for name in dictate dictate-menu dictate-cancel dictate-listen dictate-chat; do
    case $name in
    dictate) label="Dictate (toggle)" exec="$HOME/.local/bin/dictate type" ;;
    dictate-menu) label="Dictate menu" exec="$HOME/.local/bin/dictate-menu" ;;
    dictate-cancel) label="Dictate cancel" exec="$HOME/.local/bin/dictate cancel" ;;
    dictate-listen) label="Dictate always-on (toggle)" exec="$HOME/.local/bin/dictate listen --toggle" ;;
    dictate-chat) label="Ask the AI (voice chat)" exec="$HOME/.local/bin/dictate-chat" ;;
    esac
    cat >~/.local/share/applications/$name.desktop <<EOF
[Desktop Entry]
Type=Application
Name=$label
Exec=$exec
Icon=$here/docs/assets/logo.png
NoDisplay=true
StartupNotify=false
EOF
done


# Systemd user units for always-on capture + daily archive gc. Installed
# but never enabled here: an always-hot mic must be an explicit choice.
mkdir -p ~/.config/systemd/user
for unit in dictatr-listen.service dictatr-gc.service dictatr-gc.timer; do
    sed "s|@REPO@|$here|" "$here/systemd/$unit" >~/.config/systemd/user/$unit
done
command -v systemctl >/dev/null && systemctl --user daemon-reload || true

echo "Installed. The tray is running and will offer setup: engine,"
echo "typing permission, hotkeys, and a test dictation. Or run it now:"
echo "  dictate setup"
echo
echo "Optional, always-on capture (archives everything you say; opt-in):"
echo "  systemctl --user enable --now dictatr-listen"
echo "  systemctl --user enable --now dictatr-gc.timer   # daily junk sweep"
