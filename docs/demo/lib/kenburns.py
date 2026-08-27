"""Post-production: eased camera (zoom/pan) over a 4K stage recording.

The recording is captured at 3840x2160; the camera is a crop window that
glides between keyframes and is rendered out at 1920x1080, so every zoom
magnifies real pixels. Keyframe times are anchored to cues (events logged
during the scene) and the recording is synchronized to the cue clock by a
sync flash: the scene blanks the output for an instant before starting,
and the first big frame delta marks that cue in video time.

Camera plan (JSON):
  {"keyframes": [
      {"at": {"t": 0.0},                        "zoom": 1.0, "center": [960, 540]},
      {"at": {"cue": "speech_started", "offset": -0.4}, "zoom": 1.5, "center": [1600, 140]},
      ...
   ],
   "end": {"cue": "type_end", "offset": 3.0}}

"t" times are seconds after the scene_zero cue; camera state is
smoothstep-interpolated between consecutive keyframes (repeat values to
hold). Audio placement: --audio path@cue aligns the wav so its first
speech chunk lands on that cue (VAD-matched, same RMS rule as the stub).
"""

import argparse
import json
import struct
import subprocess
import wave
from pathlib import Path

from PIL import Image

# Source size comes from the recording itself (the stage's physical
# resolution); output is the logical size — every zoom magnifies real
# pixels. Set in render() from ffprobe.
SRC_W = SRC_H = OUT_W = OUT_H = 0
SCAN_W, SCAN_H = 192, 108


def smootherstep(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * p * (p * (6 * p - 15) + 10)


def load_cues(path: str) -> list[dict]:
    return [json.loads(line) for line in
            Path(path).read_text().splitlines() if line]


def cue_time(cues, name: str, index: int = 0) -> float:
    hits = [c["t"] for c in cues if c["event"] == name]
    if not hits:
        raise SystemExit(f"cue {name!r} never fired (have: "
                         f"{sorted({c['event'] for c in cues})})")
    return hits[index]


def find_flash(video: str, fps: float) -> float:  # noqa: D401
    """Video time of the sync flash: first frame that differs sharply
    from the first frame."""
    # fps first: the capture is damage-driven VFR (a headless compositor
    # renders nothing while the screen is static) — this rebuilds a
    # constant-rate timeline from the real capture timestamps.
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", video, "-t", "8",
         "-vf", f"fps={fps},scale={SCAN_W}:{SCAN_H}", "-f", "rawvideo",
         "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE)
    size = SCAN_W * SCAN_H
    first = None
    i = 0
    try:
        while True:
            frame = proc.stdout.read(size)
            if len(frame) < size:
                break
            if first is None:
                first = frame
            else:
                diff = sum(abs(a - b) for a, b in zip(frame, first)) / size
                if diff > 25:
                    return i / fps
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()
    raise SystemExit("sync flash not found in recording")


def wav_speech_offset(path: str, threshold: float = 0.02) -> float:
    """Seconds into the wav where speech first exceeds the VAD threshold."""
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        chunk = rate * 30 // 1000
        t = 0.0
        while True:
            pcm = w.readframes(chunk)
            if not pcm:
                return 0.0
            n = len(pcm) // 2
            samples = struct.unpack(f"<{n}h", pcm)
            rms = (sum(s * s for s in samples) / n) ** 0.5 / 32768.0
            if rms > threshold:
                return t
            t += len(pcm) / 2 / rate


def _find_font(bold=True):
    import glob
    for pat in ("/usr/share/fonts/**/Cantarell-VF*",
                "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/**/NotoSans-Bold*"):
        hits = glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    return None


class KeycapBadge:
    """Screen-space hotkey badge ('Ctrl + Alt + D') with fade in/out,
    drawn like a screencast key HUD at the bottom center."""

    def __init__(self, text: str, t_from: float, t_until: float):
        from PIL import ImageDraw, ImageFont
        self.t_from, self.t_until = t_from, t_until
        font = ImageFont.truetype(_find_font(), 30)
        keys = [k.strip() for k in text.split("+")]
        pad_x, pad_y, gap, joiner_w = 22, 12, 16, 26
        widths = []
        for k in keys:
            box = font.getbbox(k)
            widths.append(box[2] - box[0] + 2 * pad_x)
        asc, desc = font.getmetrics()
        key_h = asc + desc + 2 * pad_y
        total = sum(widths) + (len(keys) - 1) * (gap * 2 + joiner_w)
        img = Image.new("RGBA", (total, key_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        x = 0
        for i, (k, w) in enumerate(zip(keys, widths)):
            draw.rounded_rectangle(
                (x, 0, x + w, key_h - 1), radius=12,
                fill=(28, 29, 34, 238), outline=(138, 180, 248, 200),
                width=2)
            draw.text((x + w / 2, pad_y), k, font=font, anchor="ma",
                      fill=(232, 234, 241, 255))
            x += w
            if i < len(keys) - 1:
                draw.text((x + gap + joiner_w / 2, pad_y), "+", font=font,
                          anchor="ma", fill=(28, 29, 34, 220))
                x += gap * 2 + joiner_w
        self.img = img

    def alpha_at(self, t: float) -> float:
        fade = 0.25
        if t < self.t_from or t > self.t_until:
            return 0.0
        a = min(1.0, (t - self.t_from) / fade, (self.t_until - t) / fade)
        return max(0.0, a)

    def composite(self, frame: Image.Image, t: float):
        a = self.alpha_at(t)
        if a <= 0:
            return frame
        badge = self.img
        if a < 1.0:
            badge = badge.copy()
            badge.putalpha(badge.getchannel("A").point(
                lambda v: int(v * a)))
        x = (frame.width - badge.width) // 2
        y = frame.height - badge.height - 64
        frame.paste(badge, (x, y), badge)
        return frame


class CaptionOverlay:
    """The spoken words, revealed word-by-word across the speech window,
    with a pulsing record dot — the video is silent, so this HUD is what
    makes 'someone is dictating right now' legible."""

    def __init__(self, text: str, t_from: float, t_until: float,
                 out_w: int):
        from PIL import ImageFont
        self.words = text.split()
        self.t_from, self.t_until = t_from, t_until
        self.font = ImageFont.truetype(_find_font(), 26)
        self.max_w = int(out_w * 0.66)

    def composite(self, frame: Image.Image, t: float):
        from PIL import ImageDraw
        import math
        fade = 0.3
        a = min(1.0, (t - self.t_from) / fade, (self.t_until - t) / fade)
        if a <= 0:
            return frame
        # words reveal linearly over the first 92% of the window
        span = (self.t_until - self.t_from) * 0.92
        frac = min(1.0, max(0.0, (t - self.t_from) / span))
        n = max(1, int(len(self.words) * frac + 0.999))

        # wrap the revealed words against the max panel width
        lines, cur = [], ""
        for word in self.words[:n]:
            cand = f"{cur} {word}".strip()
            if self.font.getlength(cand) > self.max_w and cur:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        lines.append(cur)

        pad_x, pad_y, dot_r, gap = 20, 12, 8, 14
        asc, desc = self.font.getmetrics()
        line_h = asc + desc
        text_w = max(self.font.getlength(ln) for ln in lines)
        h = line_h * len(lines) + 2 * pad_y
        w = int(text_w) + 2 * pad_x + 2 * dot_r + gap
        panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(panel)
        draw.rounded_rectangle((0, 0, w - 1, h - 1),
                               radius=(line_h + 2 * pad_y) // 2,
                               fill=(28, 29, 34, 235))
        pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.1))
        cy0 = pad_y + line_h / 2
        draw.ellipse((pad_x, cy0 - dot_r, pad_x + 2 * dot_r, cy0 + dot_r),
                     fill=(242, 139, 130, int(255 * pulse)))
        for i, ln in enumerate(lines):
            draw.text((pad_x + 2 * dot_r + gap, pad_y + i * line_h), ln,
                      font=self.font, fill=(232, 234, 241, 255))
        if a < 1.0:
            panel.putalpha(panel.getchannel("A").point(
                lambda v: int(v * a)))
        x = (frame.width - panel.width) // 2
        y = frame.height - panel.height - 60
        frame.paste(panel, (x, y), panel)
        return frame


class Camera:
    def __init__(self, keyframes: list[dict]):
        self.keys = keyframes  # [(t, zoom, cx, cy)] sorted

    def at(self, t: float):
        keys = self.keys
        if t <= keys[0][0]:
            _, z, cx, cy = keys[0]
            return z, cx, cy
        for (t0, z0, x0, y0), (t1, z1, x1, y1) in zip(keys, keys[1:]):
            if t <= t1:
                e = smootherstep((t - t0) / (t1 - t0)) if t1 > t0 else 1.0
                return (z0 + (z1 - z0) * e, x0 + (x1 - x0) * e,
                        y0 + (y1 - y0) * e)
        _, z, cx, cy = keys[-1]
        return z, cx, cy


def render(args):
    plan = json.loads(Path(args.plan).read_text())
    cues = load_cues(args.cues)

    global SRC_W, SRC_H, OUT_W, OUT_H
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,width,height",
         "-of", "csv=p=0", args.video],
        capture_output=True, text=True, check=True)
    w, h, rate = probe.stdout.strip().split(",")
    SRC_W, SRC_H = int(w), int(h)
    OUT_W, OUT_H = SRC_W // 2, SRC_H // 2
    num, den = rate.split("/")
    fps = int(num) / int(den)
    if not (1 <= fps <= 120):
        fps = 30.0   # VFR container reporting nonsense

    flash_video_t = find_flash(args.video, fps)
    flash_epoch = cue_time(cues, "sync_flash")
    zero_epoch = cue_time(cues, "scene_zero")

    def to_scene(epoch: float) -> float:
        """Cue epoch -> seconds after scene_zero."""
        return epoch - zero_epoch

    def resolve(at: dict) -> float:
        if "t" in at:
            return at["t"]
        return (to_scene(cue_time(cues, at["cue"], at.get("index", 0)))
                + at.get("offset", 0.0))

    keys = sorted(
        (resolve(k["at"]), float(k["zoom"]),
         float(k["center"][0]), float(k["center"][1]))
        for k in plan["keyframes"])
    camera = Camera(keys)
    end_t = resolve(plan["end"])
    overlays = [KeycapBadge(o["text"], resolve(o["from"]),
                            resolve(o["until"]))
                for o in plan.get("overlays", [])]
    overlays += [CaptionOverlay(c["text"], resolve(c["from"]),
                                resolve(c["until"]), OUT_W)
                 for c in plan.get("captions", [])]

    # scene_zero in video time
    zero_video_t = flash_video_t + (zero_epoch - flash_epoch)

    # Pre-sample the camera per output frame, then low-pass it: raw
    # piecewise easing halts dead at every keyframe and re-accelerates,
    # which reads as jank — a light Gaussian over the track keeps the
    # velocity continuous through chained moves.
    first_frame = int(zero_video_t * fps)
    last_frame = int((zero_video_t + end_t) * fps) + 1
    track = [camera.at(j / fps - zero_video_t)
             for j in range(first_frame, last_frame + 1)]
    sigma = 0.14 * fps
    radius = int(3 * sigma)
    kernel = [pow(2.718281828, -(k * k) / (2 * sigma * sigma))
              for k in range(-radius, radius + 1)]
    ksum = sum(kernel)

    def smoothed(idx: int):
        vals = [0.0, 0.0, 0.0]
        for k, wgt in zip(range(-radius, radius + 1), kernel):
            j = min(max(idx + k, 0), len(track) - 1)
            for c in range(3):
                vals[c] += track[j][c] * wgt
        return tuple(v / ksum for v in vals)

    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", args.video,
         "-vf", f"fps={fps}",   # VFR capture -> constant-rate timeline
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    tmp_video = args.out + ".video.mp4"
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{OUT_W}x{OUT_H}", "-r", f"{fps}", "-i", "-",
         "-c:v", "libopenh264", "-b:v", "8M",
         "-pix_fmt", "yuv420p", tmp_video],
        stdin=subprocess.PIPE)

    frame_bytes = SRC_W * SRC_H * 3
    i = 0
    rendered = 0
    last_raw = None
    while True:
        raw = dec.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            # The capture is damage-driven: a static closing hold emits
            # no frames at all. Freeze-frame the last real one so the
            # plan's ending isn't cut short.
            if last_raw is None:
                break
            raw = last_raw
        last_raw = raw
        t = i / fps - zero_video_t
        i += 1
        if t < 0:
            continue
        if t > end_t:
            break
        zoom, cx, cy = smoothed(i - 1 - first_frame)
        w = SRC_W / zoom
        h = SRC_H / zoom
        x = min(max(cx * 2 - w / 2, 0), SRC_W - w)
        y = min(max(cy * 2 - h / 2, 0), SRC_H - h)
        img = Image.frombytes("RGB", (SRC_W, SRC_H), raw)
        img = img.resize((OUT_W, OUT_H), Image.LANCZOS,
                         box=(x, y, x + w, y + h))
        for badge in overlays:
            img = badge.composite(img, t)
        enc.stdin.write(img.tobytes())
        rendered += 1
    dec.stdout.close()
    dec.terminate()
    enc.stdin.close()
    enc.wait()
    print(f"rendered {rendered} frames "
          f"({rendered / fps:.1f}s from scene_zero at {zero_video_t:.2f}s)")

    if args.audio:
        wav_path, _, cue_name = args.audio.partition("@")
        speech_scene_t = to_scene(cue_time(cues, cue_name))
        delay_s = speech_scene_t - wav_speech_offset(wav_path)
        delay_ms = max(0, int(delay_s * 1000))
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", tmp_video, "-i", wav_path,
             "-filter_complex",
             f"[1:a]adelay={delay_ms}|{delay_ms},apad[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "128k", "-shortest",
             "-movflags", "+faststart", args.out],
            check=True)
        Path(tmp_video).unlink()
    else:
        Path(tmp_video).rename(args.out)

    if args.gif:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", args.out,
             "-filter_complex",
             "[0:v]fps=15,scale=840:-1:flags=lanczos,split[a][b];"
             "[a]palettegen=stats_mode=diff[p];"
             "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
             args.gif],
            check=True)
    print(f"wrote {args.out}" + (f" and {args.gif}" if args.gif else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--cues", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--audio", help="voice.wav@cue_name (VAD-aligned)")
    ap.add_argument("--gif")
    render(ap.parse_args())


if __name__ == "__main__":
    main()
