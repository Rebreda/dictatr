"""Scene direction: drive the stage (pointer, windows, captures, cues).

A Director wraps a running Session with everything a scene script needs:
sway IPC queries, eased pointer glides, clicks, cue logging/waiting,
screenshots (grim) and recordings (wf-recorder). All coordinates are
logical (1920x1080); captures are physical (3840x2160).
"""

import json
import math
import subprocess
import time

from session import Session, SCALE, W, H


def _smootherstep(p: float) -> float:
    return p * p * p * (p * (6 * p - 15) + 10)


class Director:
    def __init__(self, session: Session):
        self.s = session
        self.pointer = (W // 2, H // 2)
        self.recorder = None

    # -- sway IPC -------------------------------------------------------
    def swaymsg(self, *args, parse=False):
        r = subprocess.run(["swaymsg", *args], env=self.s.env,
                           capture_output=True, text=True, check=True)
        return json.loads(r.stdout) if parse else r.stdout

    def window_rect(self, app_id: str) -> dict | None:
        tree = self.swaymsg("-t", "get_tree", parse=True)
        stack = [tree]
        while stack:
            node = stack.pop()
            if node.get("app_id") == app_id:
                return node["rect"]
            stack.extend(node.get("nodes", []) + node.get("floating_nodes", []))
        return None

    def wait_window(self, app_id: str, timeout: float = 10) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            rect = self.window_rect(app_id)
            if rect:
                return rect
            time.sleep(0.1)
        raise TimeoutError(f"window {app_id} did not appear")

    # -- pointer --------------------------------------------------------
    def _ptr(self, **cmd):
        self.s.pointerd.stdin.write(json.dumps(cmd) + "\n")
        self.s.pointerd.stdin.flush()

    def move_to(self, x, y):
        self._ptr(op="move", x=int(x), y=int(y))
        self.pointer = (x, y)

    def glide_to(self, x, y, duration: float = 0.6):
        """Eased pointer motion, ~90 updates/s."""
        x0, y0 = self.pointer
        steps = max(2, int(duration * 90))
        for i in range(1, steps + 1):
            e = _smootherstep(i / steps)
            self._ptr(op="move", x=int(x0 + (x - x0) * e),
                      y=int(y0 + (y - y0) * e))
            time.sleep(duration / steps)
        self.pointer = (x, y)

    def click(self, btn: str = "left"):
        self._ptr(op="button", btn=btn, state=1)
        time.sleep(0.06)
        self._ptr(op="button", btn=btn, state=0)

    # -- cues -----------------------------------------------------------
    def cue(self, event: str, **kw):
        with open(self.s.cues_path, "a") as f:
            f.write(json.dumps({"t": time.time(), "event": event, **kw})
                    + "\n")

    def cues(self) -> list[dict]:
        return [json.loads(line) for line in
                self.s.cues_path.read_text().splitlines() if line]

    def wait_cue(self, event: str, timeout: float = 30, after: float = 0.0
                 ) -> dict:
        """Block until a cue named *event* (logged after time *after*)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for c in self.cues():
                if c["event"] == event and c["t"] > after:
                    return c
            time.sleep(0.05)
        raise TimeoutError(f"cue {event} not seen within {timeout}s")

    # -- capture --------------------------------------------------------
    def screenshot(self, path, crop=None, scale=None):
        """grim the stage; crop is a logical (x, y, w, h) box; scale is
        an output (w, h) — e.g. (1920, 1080) to downsample the 4K grab."""
        subprocess.run(["grim", "-c", str(path)], env=self.s.env, check=True)
        if crop or scale:
            from PIL import Image
            img = Image.open(path)
            if crop:
                x, y, w, h = (int(v * SCALE) for v in crop)
                img = img.crop((x, y, x + w, y + h))
            if scale:
                img = img.resize(scale, Image.LANCZOS)
            img.save(path)

    def start_recording(self, path, framerate: int = 30):
        # ffv1: lossless capture (the camera pass re-encodes), and it
        # exists in Fedora's patent-free ffmpeg unlike libx264. Sliced +
        # threaded, or the encoder falls behind 4K@30 and truncates.
        self.recorder = subprocess.Popen(
            ["wf-recorder", "-o", "HEADLESS-1", "-f", str(path),
             "-r", str(framerate), "-c", "ffv1",
             "-p", "slices=16", "-p", "threads=8"],
            env=self.s.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)  # let capture spin up; post trims via scene_zero

    def stop_recording(self):
        import signal
        self.recorder.send_signal(signal.SIGINT)   # graceful finalize
        # wf-recorder only notices the signal when a frame event arrives,
        # and a static screen produces none — wiggle the cursor until it
        # wakes up and exits.
        x, y = self.pointer
        for i in range(100):
            if self.recorder.poll() is not None:
                break
            self._ptr(op="move", x=int(x + i % 2), y=int(y))
            time.sleep(0.1)
        try:
            self.recorder.wait(20)
        except subprocess.TimeoutExpired:
            self.recorder.terminate()
            self.recorder.wait(10)
        self.recorder = None

    def stream_type(self, text: str, duration: float):
        """Type *text* into the focused surface char-by-char, paced to
        span *duration* — the 'live dictation' illusion. One wtype
        process (-s settles the keymap once, -d paces every key)."""
        per_char_ms = max(12, int(duration * 1000 / max(1, len(text))))
        subprocess.run(["wtype", "-s", "150", "-d", str(per_char_ms),
                        "--", text], env=self.s.env, check=False)

    # -- apps -----------------------------------------------------------
    def run_app(self, cmd, wait=False, **env_extra):
        if wait:
            return subprocess.run(cmd, env=self.s.app_env(**env_extra),
                                  capture_output=True, text=True)
        return subprocess.Popen(cmd, env=self.s.app_env(**env_extra),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
