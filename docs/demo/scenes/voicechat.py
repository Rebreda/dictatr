"""Voice-chat video: a two-turn conversation with the AI, hands-free.

Storyboard (about 28 seconds):
  a working desk — the DM where Robin asked where things stand,
  a browser off to the side
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

import sys
import time

from director import Director
from hero import speech_duration
from session import DEMO, REPO, W, H

sys.path.insert(0, str(REPO / "ui"))
import radial_layout  # noqa: E402  (the ring's own geometry)

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


# The set (logical px). Two props, both backdrop: the DM that motivates
# the question on the left, a browser on the right. Neither says
# anything the viewer has to read — the eye belongs on the chat column
# in the middle alley.
#
# Overlay surfaces never see pointer motion on the headless stage, so
# menu and chat both settle at their FALLBACK spots: the menu at the
# surface center, the chat hub at 62% height. The desk is built around
# those two points (measured on the 1280x720 stage).
DM_X, DM_Y, DM_W, DM_H = 20, 150, 440, 476
BR_X, BR_Y, BR_W, BR_H = 832, 128, 440, 430

MENU_CX, MENU_CY = W // 2, 371     # overlay surface center (below the bar)
# "Ask the AI" is bubble 3 of the 6-bubble root ring (dictate,
# clipboard, ask, always-on, More, cancel), asked for rather than
# measured: the orbit is the ring's business and it adapts to the item
# count, so a number written here goes stale without saying so.
_DX, _DY = radial_layout.slot_offset(6, 2)
CHAT_BUBBLE = (MENU_CX + _DX, MENU_CY + _DY)
HUB = (W // 2, 456)                # chat fallback: hub at 62% height
PARK = (HUB[0] + 118, HUB[1] + 44)  # cursor rest: clear of the satellites
# Camera target for the conversation. Sits BELOW the hub on purpose:
# it lifts the whole column in frame, leaving the bottom strip free
# for the caption HUD instead of letting it collide with the hub.
COLUMN = (HUB[0], 358)

CAMERA_PLAN = {
    "keyframes": [
        {"at": {"t": 0.0}, "zoom": 1.0, "center": [W // 2, H // 2]},
        {"at": {"cue": "menu_open", "offset": 1.4},
         "zoom": 1.0, "center": [W // 2, H // 2]},
        {"at": {"cue": "menu_open", "offset": 2.6},
         "zoom": 1.3, "center": [MENU_CX, MENU_CY - 20]},
        {"at": {"cue": "menu_click", "offset": 0.4},
         "zoom": 1.3, "center": [MENU_CX, MENU_CY - 20]},
        # Settle on the chat column for both turns of the conversation.
        {"at": {"cue": "menu_click", "offset": 1.6},
         "zoom": 1.28, "center": list(COLUMN)},
        {"at": {"cue": "chat_answer", "index": 1, "offset": 1.8},
         "zoom": 1.28, "center": list(COLUMN)},
        {"at": {"cue": "chat_answer", "index": 1, "offset": 3.6},
         "zoom": 1.0, "center": [W // 2, H // 2]},
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
    """The desk: tray, the DM that prompts the question, a browser."""
    d.s.start_tray()
    for prop, args, (x, y, w, h) in (
            ("browser", [], (BR_X, BR_Y, BR_W, BR_H)),
            ("chat", ["--idle"], (DM_X, DM_Y, DM_W, DM_H))):
        d.run_app(["python3", str(DEMO / f"stage/{prop}.py"), *args])
        d.wait_window(f"demo.{prop}")
        d.swaymsg(f'[app_id="demo.{prop}"] resize set {w} {h}, '
                  f'move position {x} {y}')
    # Focus the backdrop, not the DM composer — no focus ring pulling
    # the eye toward the entry.
    d.swaymsg('[app_id="demo.browser"] focus')
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
