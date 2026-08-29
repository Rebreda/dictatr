"""Full-desktop stills: the whole staged desktop, not isolated widgets.

Every shot shares the hero scene's set (wallpaper, top bar with the tray
icon, the notes editor) so stills and video read as one desktop.

  desktop-menu.png   radial menu bloomed at the cursor, mid-work
  desktop-setup.png  the setup wizard, mid-walk
"""

import shutil
import time

from director import Director
from session import DEMO, REPO, STAGE, W, H

MENU_APP_ID = "io.github.rebreda.dictatr.menu"

# Editor placement, shared with the hero video scene. Sized so the
# notes fill the window instead of floating in an empty slab.
ED_X, ED_Y, ED_W, ED_H = 88, 128, 720, 384
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


SETUP_APP_ID = "io.github.rebreda.dictatr.setup"

# The menu still gets its own backdrop: two mute props flanking the
# middle alley, so the ring blooms over clean wallpaper instead of
# straddling a window edge and chewing through text.
WR_X, WR_Y, WR_W, WR_H = 40, 130, 420, 500
BR_X, BR_Y, BR_W, BR_H = 830, 140, 430, 420


def desk(d: Director):
    """Backdrop for the overlay stills: a word processor and a browser,
    both deliberately unreadable — the dictatr surface is the subject."""
    d.s.start_tray()
    for prop, (x, y, w, h) in (("writer", (WR_X, WR_Y, WR_W, WR_H)),
                               ("browser", (BR_X, BR_Y, BR_W, BR_H))):
        d.run_app(["python3", str(DEMO / f"stage/{prop}.py")])
        d.wait_window(f"demo.{prop}")
        d.swaymsg(f'[app_id="demo.{prop}"] resize set {w} {h}, '
                  f'move position {x} {y}')
    time.sleep(1.5)


def menu(d: Director, out: str):
    desk(d)
    _fake_listen_live(d)
    # Park the cursor at the overlay's center: whether placement comes
    # from a pointer event or the app's centered fallback, menu and
    # cursor land together — the composition is identical either way.
    cx, cy = W // 2, 371
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


def setup(d: Director, out: str, step: int = 2):
    """The wizard on the same desk.

    It is a layer-shell overlay now, like the menu, so sway's window
    tree cannot see it: no wait_window, no move, just the clock. It
    centers itself, which is where the composition wants it anyway.

    The last step is the shot worth having: the ring offers the two
    choices that end the walk, and its text does not depend on what the
    machine has. The earlier steps report this stage's private bus (no
    portals) rather than what a real desktop would say.
    """
    # Own config dir: the wizard writes a setup_done key when it closes,
    # and the stage's checked-in config.toml is not its to edit.
    d.run_app([str(REPO / "bin/dictate-setup")],
              DICTATR_SETUP_STEP=str(step), HOME="/home/user",
              XDG_CONFIG_HOME=str(d.s.run / "wizard-config"))
    time.sleep(4.0)               # GTK startup, probe, ring twirl
    d.move_to(W - 150, H - 150)   # cursor off the card, on open wallpaper
    time.sleep(0.6)
    d.screenshot(out, scale=(W, H))


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
