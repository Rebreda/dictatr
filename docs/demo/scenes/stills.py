"""Full-desktop stills: the whole staged desktop, not isolated widgets.

Both shots share the hero scene's set — wallpaper, top bar with the tray
icon, and the notes editor — so stills and video read as one desktop.

  desktop-menu.png      radial menu bloomed at the cursor, mid-work
  desktop-settings.png  settings window floating on the same desktop
"""

import shutil
import time

from director import Director
from session import REPO, STAGE, W, H

MENU_APP_ID = "io.github.rebreda.dictatr.menu"

# Editor placement, shared with the hero video scene. Sized so the
# notes fill the window instead of floating in an empty slab.
ED_X, ED_Y, ED_W, ED_H = 110, 160, 900, 480
# nano cursor: line 7 col 3 of stage/notes.md ("- " continuation bullet)
NANO_POS = "+7,3"


def _fake_listen_live(d: Director):
    """A live pid in listen.pid lights the 'always-on capture' bubble
    green and flips the tray icon to recording — the state worth showing."""
    run = d.s.run / "xdg-run" / "dictatr"
    run.mkdir(parents=True, exist_ok=True)
    sway_pid = next(p.pid for name, p in d.s.procs if name == "sway")
    (run / "listen.pid").write_text(str(sway_pid))


def desktop(d: Director):
    """Dress the set: tray, editor with notes, parked cursor."""
    d.s.start_tray()
    notes = d.s.run / "notes.md"
    shutil.copy(STAGE / "notes.md", notes)
    d.run_app(["foot", "--app-id=demo-editor", "--title=notes.md",
               "-e", "nano", "--zero", NANO_POS, str(notes)])
    d.wait_window("demo-editor")
    d.swaymsg(f'[app_id="demo-editor"] resize set {ED_W} {ED_H}, '
              f'move position {ED_X} {ED_Y}')
    time.sleep(1.5)   # tray registered, editor painted


def menu(d: Director, out: str):
    _fake_listen_live(d)
    # Park the cursor at the overlay's center: whether placement comes
    # from a pointer event or the app's centered fallback, menu and
    # cursor land together — the composition is identical either way.
    cx, cy = 800, 461
    d.move_to(cx, cy)
    time.sleep(0.2)
    d.run_app([str(REPO / "bin/dictate-menu")])
    # The overlay menu is a layer-shell surface — invisible to sway's
    # window tree — so wait on the clock: GTK startup + twirl + settle.
    # Jiggle the pointer the whole time it maps: sway only tells the
    # overlay where the cursor is via a motion event, and the menu
    # blooms wherever the first one lands.
    for i in range(45):
        d.move_to(cx + i % 2, cy)
        time.sleep(0.08)
    time.sleep(0.8)
    # Rest the cursor between hub and bubbles: no hover wash, no tooltip.
    d.glide_to(cx + 38, cy - 38, 0.4)
    time.sleep(0.3)
    d.screenshot(out, scale=(W, H))
    d.run_app([str(REPO / "bin/dictate-menu")])   # toggle closed
    time.sleep(1.0)


def settings(d: Director, out: str):
    # Launch without bin/dictate-menu: its gtk4-layer-shell LD_PRELOAD
    # turns even this plain window into a fullscreen layer surface on
    # the demo stage. The settings window needs no layer-shell.
    # Sanitized paths for the shot: a fake HOME and an empty
    # XDG_CONFIG_HOME make the archive/config rows read like a fresh
    # install instead of leaking demo-runtime paths.
    d.run_app(["python3", str(REPO / "ui/menu.py"), "--settings"],
              HOME="/home/user", XDG_CONFIG_HOME="", DICTATE_ARCHIVE="")
    rect = d.wait_window(MENU_APP_ID)
    # Center-right so the editor stays visible behind it.
    d.swaymsg(f'[app_id="{MENU_APP_ID}"] move position '
              f'{W - rect["width"] - 90} '
              f'{(H - rect["height"]) // 2}')
    d.move_to(W - 120, H - 160)   # park the cursor on open wallpaper
    time.sleep(1.8)               # model list fetch (stub) + first paint
    d.screenshot(out, scale=(W, H))
