"""Voice-chat video: a two-turn conversation with the AI, hands-free.

Storyboard (about 28 seconds):
  a lived-in desk — the DM window where Robin just asked "where do
  things stand?", the notes editor behind it
  ->  the radial menu blooms; pointer CLICKS the "Ask the AI" bubble
  ->  the chat hub lands at the pointer; the mic goes green
  ->  turn 1: the spoken request streams word-by-word into a green
      pill, thinking dots, the drafted answer blooms beneath
  ->  turn 2: "Make it snappier." — the AI tightens its own draft,
      proof the conversation has memory
  ->  pull back wide; the mic re-arms, listening.

The app under capture is the real ui/chat.py end to end: the canned
voice enters through DICTATE_INPUT (one wav per turn), the stub streams
scripted word-level deltas paced to the audio, and the answers come from
the stub's chat endpoint. Nothing in the UI is faked.
"""

import shutil
import time

from director import Director
from hero import speech_duration
from session import DEMO, REPO, STAGE, W, H

TRANSCRIPTS = [
    "Draft a status update for Robin: tray rewrite merged, voice "
    "detection tuned, release candidate tomorrow morning.",
    "Make it snappier.",
]
ANSWERS = [
    "Here’s a draft: “Tray rewrite is merged and voice "
    "detection is tuned — on track to cut a release candidate "
    "tomorrow morning.”",
    "“Tray and voice detection: done. RC1 lands tomorrow "
    "morning.”",
]


def scenario(voices: list[str]) -> dict:
    """Delta pacing per utterance: one word per equal slice of the
    spoken span, so the live pill trails the (silent) voice naturally."""
    intervals = [
        int(speech_duration(wav) * 1000 / max(1, len(line.split())))
        for wav, line in zip(voices, TRANSCRIPTS)
    ]
    return {
        "transcripts": TRANSCRIPTS,
        "chat_answers": ANSWERS,
        "transcribe_delay_ms": 450,
        "chat_delay_s": 1.5,
        "delta_word_ms": intervals,
    }


# The set (logical px): a triptych. DM window left, notes editor right
# (smaller type — it's set dressing), and the center alley belongs to
# the chat column. Overlay surfaces never see pointer motion on the
# headless stage, so menu and chat both settle at their FALLBACK spots:
# the menu at the surface center, the chat hub low-center (ui/chat.py
# places it at 62% height). The desk is designed around those points.
ED_X, ED_Y, ED_W, ED_H = 980, 170, 600, 400
ED_FONT = "font=Noto Sans Mono:size=10.5"   # 65 cols fit in 600px
DM_X, DM_Y, DM_W, DM_H = 60, 200, 560, 600
NANO_POS = "+7,3"   # continuation bullet in stage/notes.md

MENU_CX, MENU_CY = 800, 461      # overlay surface center (below the bar)
# "Ask the AI" is action index 3 of 7: angle -90° + 3·(360/7)°, r=84.
CHAT_BUBBLE = (MENU_CX + 36, MENU_CY + 76)
HUB = (800, 566)                 # chat fallback: hub low-center
PARK = (HUB[0] + 122, HUB[1] + 48)  # cursor rest: clear of the satellites
COLUMN = (HUB[0], 430)           # chat column mid-height, for the camera

CAMERA_PLAN = {
    "keyframes": [
        {"at": {"t": 0.0}, "zoom": 1.0, "center": [800, 450]},
        {"at": {"cue": "menu_open", "offset": 1.4},
         "zoom": 1.0, "center": [800, 450]},
        {"at": {"cue": "menu_open", "offset": 2.6},
         "zoom": 1.35, "center": [MENU_CX, MENU_CY - 30]},
        {"at": {"cue": "menu_click", "offset": 0.4},
         "zoom": 1.35, "center": [MENU_CX, MENU_CY - 30]},
        # Settle on the chat column for both turns of the conversation.
        {"at": {"cue": "menu_click", "offset": 1.6},
         "zoom": 1.5, "center": list(COLUMN)},
        {"at": {"cue": "chat_answer", "index": 1, "offset": 1.8},
         "zoom": 1.5, "center": list(COLUMN)},
        {"at": {"cue": "chat_answer", "index": 1, "offset": 3.6},
         "zoom": 1.0, "center": [800, 450]},
    ],
    "end": {"cue": "chat_answer", "index": 1, "offset": 5.4},
    # The spoken turns, as live caption bubbles — the voice track of a
    # silent gif.
    "captions": [
        {"text": "“" + TRANSCRIPTS[0] + "”",
         "from": {"cue": "speech_started", "offset": -0.05},
         "until": {"cue": "speech_stopped", "offset": 0.35}},
        {"text": "“" + TRANSCRIPTS[1] + "”",
         "from": {"cue": "speech_started", "index": 1, "offset": -0.05},
         "until": {"cue": "speech_stopped", "index": 1, "offset": 0.35}},
    ],
}


def dress(d: Director):
    """The desk: tray, DM conversation left, notes editor right."""
    d.s.start_tray()
    notes = d.s.run / "notes.md"
    shutil.copy(STAGE / "notes.md", notes)
    d.run_app(["foot", "--app-id=demo-editor", "--title=notes.md",
               "-o", ED_FONT,
               "-e", "nano", "--zero", NANO_POS, str(notes)])
    d.wait_window("demo-editor")
    d.swaymsg(f'[app_id="demo-editor"] resize set {ED_W} {ED_H}, '
              f'move position {ED_X} {ED_Y}')
    d.run_app(["python3", str(DEMO / "stage/chat.py")])
    d.wait_window("demo.chat")
    d.swaymsg(f'[app_id="demo.chat"] resize set {DM_W} {DM_H}, '
              f'move position {DM_X} {DM_Y}')
    # Editor keeps keyboard focus: solid nano cursor, and no focus ring
    # on the DM composer pulling the eye.
    d.swaymsg('[app_id="demo-editor"] focus')
    time.sleep(1.5)   # tray registered, windows painted


def _settle_chat(d: Director):
    """Wait out the chat's placement (pointer poll -> fallback), then
    rest the cursor beside the hub."""
    time.sleep(2.2)
    d.glide_to(*PARK, 0.5)


# Where the cursor waits while the menu blooms: the gap between hub and
# bubbles, up-right — no bubble hover, no red hub wash. (Placement is
# fallback-driven on the stage, so the menu centers itself regardless.)
MENU_REST = (MENU_CX + 38, MENU_CY - 38)


def run(d: Director, voices: list[str], raw_out: str):
    dress(d)
    d.move_to(*MENU_REST)
    time.sleep(1.5)

    d.start_recording(raw_out)
    time.sleep(0.6)

    # Sync beacon: one dark flash maps cue time onto video time in post.
    d.cue("sync_flash")
    d.swaymsg("output HEADLESS-1 bg #000000 solid_color")
    time.sleep(0.18)
    d.swaymsg(f"output HEADLESS-1 bg {d.s.gen / 'wallpaper.png'} fill")
    time.sleep(1.2)

    d.cue("scene_zero")
    time.sleep(0.8)

    # Bloom the menu; the chat it spawns inherits the canned voice.
    d.cue("menu_open")
    d.run_app([str(REPO / "bin/dictate-menu")],
              DICTATE_INPUT=":".join(voices), DICTATE_INPUT_PACED="1")
    for i in range(30):
        d.move_to(MENU_REST[0] + i % 2, MENU_REST[1])
        time.sleep(0.08)
    time.sleep(0.4)

    # Hover "Ask the AI", click it; the hub blooms just below.
    d.glide_to(*CHAT_BUBBLE, 0.7)
    time.sleep(0.5)
    d.cue("menu_click")
    d.click()
    _settle_chat(d)

    # Both turns run themselves (canned voice + scripted stub); the
    # cues drive the camera. Just wait for the second answer.
    first = d.wait_cue("chat_answer", timeout=90)
    d.wait_cue("chat_answer", timeout=90, after=first["t"])
    time.sleep(4.4)   # answer reads; pull-back happens in post
    d.stop_recording()


def still(d: Director, voices: list[str], out: str):
    """desktop-chat.png: the finished two-turn conversation — history
    fading with age, the freshest answer solid, the mic re-armed green."""
    dress(d)
    d.move_to(*HUB)
    time.sleep(0.3)
    d.run_app([str(REPO / "bin/dictate-chat")],
              DICTATE_INPUT=":".join(voices), DICTATE_INPUT_PACED="1")
    _settle_chat(d)
    first = d.wait_cue("chat_answer", timeout=90)
    d.wait_cue("chat_answer", timeout=90, after=first["t"])
    time.sleep(1.4)   # answer revealed, mic re-armed for the next turn
    d.screenshot(out, scale=(W, H))
