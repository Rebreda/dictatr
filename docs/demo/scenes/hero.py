"""Hero video: dictate a line into an editor — words land as you speak.

Storyboard (about 15 seconds):
  wide stage with tray + editor  ->  Ctrl+Alt+D keycaps, "Listening"
  ->  camera settles on the cursor; the sentence appears in real time,
      paced to the (silent) voice
  ->  speech ends, recording stops by itself, "Typed" toast
  ->  pull back wide.

The voice is a wav streamed into dictatr instead of the mic
(DICTATE_INPUT, paced); the Lemonade stub answers with the scripted
transcript. The live typing is staged by the director in sync with the
voice — the app's own end-of-utterance delivery is swallowed by the
shim so nothing types twice. The set is shared with the stills scene.
"""

import time
import wave

import stills
from director import Director
from session import REPO

TRANSCRIPT = ("Next up: cut a release candidate on Friday, and write up "
              "the archive format for the README.")

SCENARIO = {
    "transcripts": [TRANSCRIPT],
    "transcribe_delay_ms": 450,
}

CAMERA_PLAN = {
    "keyframes": [
        # Set the scene, then hold wide through the hotkey beat so the
        # keycaps and the "Listening" notification both read.
        {"at": {"t": 0.0}, "zoom": 1.0, "center": [800, 450]},
        {"at": {"cue": "speech_started", "offset": 0.5},
         "zoom": 1.0, "center": [800, 450]},
        # Then onto the cursor while the transcription lands in chunks.
        {"at": {"cue": "speech_started", "offset": 1.5},
         "zoom": 1.6, "center": [470, 330]},
        {"at": {"cue": "type_end", "offset": 0.6},
         "zoom": 1.6, "center": [470, 330]},
        {"at": {"cue": "type_end", "offset": 2.6},
         "zoom": 1.0, "center": [800, 450]},
    ],
    "end": {"cue": "type_end", "offset": 3.8},
    # Screen-space keycap badge: the hotkey that starts dictation.
    "overlays": [
        {"text": "Ctrl + Alt + D",
         "from": {"cue": "hotkey", "offset": -0.1},
         "until": {"cue": "speech_started", "offset": 0.4}},
    ],
    # The spoken words, as a live caption bubble — the voice track of a
    # silent gif; the typed chunks trail it like real streaming ASR.
    "captions": [
        {"text": "“" + TRANSCRIPT + "”",
         "from": {"cue": "speech_started", "offset": -0.05},
         "until": {"cue": "speech_stopped", "offset": 0.35}},
    ],
}


def speech_duration(voice_wav: str) -> float:
    """Length of the spoken part: total minus the fixed lead-in and
    tail silence that `demo voice` pads around it."""
    with wave.open(voice_wav, "rb") as w:
        total = w.getnframes() / w.getframerate()
    return max(2.0, total - 0.8 - 3.0)


def run(d: Director, voice_wav: str, raw_out: str):
    stills.desktop(d)
    d.move_to(1300, 620)   # park the cursor somewhere neutral

    d.start_recording(raw_out)
    time.sleep(0.6)

    # Sync beacon: one dark flash maps cue time onto video time in post.
    d.cue("sync_flash")
    d.swaymsg("output HEADLESS-1 bg #000000 solid_color")
    time.sleep(0.18)
    d.swaymsg(f"output HEADLESS-1 bg {d.s.gen / 'wallpaper.png'} fill")
    time.sleep(1.2)

    d.cue("scene_zero")
    time.sleep(0.9)
    d.cue("hotkey")        # keycap overlay anchor: the moment of "press"
    d.run_app([str(REPO / "bin/dictate"), "type"],
              DICTATE_INPUT=voice_wav, DICTATE_INPUT_PACED="1",
              DEMO_SWALLOW_TYPE="1")
    # Words appear at the cursor in real time, paced to the voice.
    d.wait_cue("speech_started", timeout=30)
    d.stream_type(TRANSCRIPT, speech_duration(voice_wav))
    d.wait_cue("type_end", timeout=60)
    time.sleep(2.6)        # let the "Typed" toast read
    d.stop_recording()
