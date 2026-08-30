"""Chat video: answer a DM by voice, via the radial menu.

Storyboard (about 22 seconds):
  a Signal-style DM sits mid-conversation ("where do things stand?")
  ->  the radial menu blooms; pointer hovers and CLICKS the dictate bubble
  ->  camera dives to the compose box; the reply appears there in real
      time, paced to the (silent) voice
  ->  hands-free stop, "Typed" toast; the send button is clicked
  ->  the reply lands in the thread as a sent bubble, pull back wide.

The menu itself spawns `dictate type`, so DICTATE_INPUT (canned voice)
and DEMO_SWALLOW_TYPE go on the menu process; the live typing is staged
by the director in sync with the voice, and the app's end-of-utterance
delivery is swallowed so nothing types twice.
"""

import sys
import time

from director import Director
from hero import speech_duration
from session import DEMO, REPO

sys.path.insert(0, str(REPO / "ui"))
import radial_layout  # noqa: E402  (the ring's own geometry)

TRANSCRIPT = ("Almost there. The tray rewrite is merged and the voice "
              "detection is tuned - I'll cut a release candidate "
              "tomorrow morning.")

SCENARIO = {
    "transcripts": [TRANSCRIPT],
    "transcribe_delay_ms": 450,
}

# Chat window placement (logical px): snug under the bar, near the
# clock. Width/sidebar must match stage/chat.py (WIN_W / SIDEBAR_W).
CHAT_X, CHAT_Y = 24, 32
CHAT_W, CHAT_H = 560, 520
MENU_CX, MENU_CY = 640, 371      # overlay surface center (below the bar)
# Bubble 1 of the 6-bubble root ring (Dictate), asked for rather than
# measured: the orbit is the ring's business and it adapts to the item
# count, so a number written here goes stale without saying so.
_DX, _DY = radial_layout.slot_offset(6, 0)
TYPE_BUBBLE = (MENU_CX + _DX, MENU_CY + _DY)
# The round send button, bottom-right of the composer.
SEND_BTN = (CHAT_X + CHAT_W - 30, CHAT_Y + CHAT_H - 28)
# The compose entry, where the transcript lands live.
COMPOSE = (CHAT_X + 168 + 180, CHAT_Y + CHAT_H - 40)

CAMERA_PLAN = {
    "keyframes": [
        {"at": {"t": 0.0}, "zoom": 1.0, "center": [640, 360]},
        {"at": {"cue": "menu_open", "offset": 1.8},
         "zoom": 1.0, "center": [640, 360]},
        {"at": {"cue": "menu_open", "offset": 3.0},
         "zoom": 1.3, "center": [616, 320]},
        {"at": {"cue": "menu_click", "offset": 0.3},
         "zoom": 1.3, "center": [616, 320]},
        # Tight on the compose box: the words land here, live.
        {"at": {"cue": "speech_started", "offset": 0.7},
         "zoom": 1.9, "center": [COMPOSE[0], COMPOSE[1] - 50]},
        {"at": {"cue": "sent", "offset": 0.4},
         "zoom": 1.9, "center": [COMPOSE[0], COMPOSE[1] - 50]},
        {"at": {"cue": "sent", "offset": 1.8},
         "zoom": 1.3, "center": [336, 320]},
        {"at": {"cue": "sent", "offset": 3.4},
         "zoom": 1.0, "center": [640, 360]},
    ],
    "end": {"cue": "sent", "offset": 4.6},
}


def run(d: Director, voice_wav: str, raw_out: str):
    d.s.start_tray()
    d.run_app(["python3", str(DEMO / "stage/chat.py")])
    d.wait_window("demo.chat")
    d.swaymsg(f'[app_id="demo.chat"] move position {CHAT_X} {CHAT_Y}')
    d.move_to(MENU_CX, MENU_CY)
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

    # Open the radial menu; dictate inherits the canned voice from it.
    d.cue("menu_open")
    d.run_app([str(REPO / "bin/dictate-menu")],
              DICTATE_INPUT=voice_wav, DICTATE_INPUT_PACED="1",
              DEMO_SWALLOW_TYPE="1")
    # Jiggle while it maps (it blooms at the first motion it sees, or
    # centered — the cursor is parked at that same center either way).
    for i in range(30):
        d.move_to(MENU_CX + i % 2, MENU_CY)
        time.sleep(0.08)
    time.sleep(0.5)

    # Hover the Dictate bubble, then click it.
    d.glide_to(*TYPE_BUBBLE, 0.7)
    time.sleep(0.7)
    d.cue("menu_click")
    d.click()

    # The reply appears in the compose box in real time with the voice.
    d.wait_cue("speech_started", timeout=30)
    d.stream_type(TRANSCRIPT, speech_duration(voice_wav))
    d.wait_cue("type_end", timeout=60)
    time.sleep(0.8)
    d.glide_to(*SEND_BTN, 0.7)   # send the reply by clicking the button
    time.sleep(0.4)
    d.cue("sent")
    d.click()
    time.sleep(3.2)
    d.stop_recording()
