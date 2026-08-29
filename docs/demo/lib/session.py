"""Demo stage: an isolated, reproducible desktop for dictatr captures.

Boots a headless sway compositor with its own session dbus, notification
daemon (mako), Lemonade stub, and a persistent virtual pointer, all fenced
off from the host desktop:

  - own Wayland display (headless wlroots output, 3840x2160 @ 2x)
  - own session bus: notifications and the tray never touch the real desktop
  - own XDG_RUNTIME_DIR for app processes: host pidfiles can't leak in
  - own XDG_CONFIG_HOME (docs/demo/stage/xdg): mako/foot/dictatr config
  - a `ydotool` shim on PATH that types via wtype (virtual-keyboard
    protocol) inside the nested compositor, with a typewriter cadence

Tool resolution: PATH first; the DICTATR_DEMO_TOOLS env var may name
colon-separated prefixes (each containing usr/bin, usr/lib64) to use
without a system install — handy for extracted RPMs.
"""

import json
import os
import shutil
import socket
import string
import subprocess
import sys
import time
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent   # docs/demo
REPO = DEMO.parent.parent
STAGE = DEMO / "stage"
OUT = DEMO / "out"

REQUIRED = ["sway", "swaymsg", "grim", "wf-recorder", "wtype", "mako",
            "foot", "rsvg-convert", "ffmpeg", "dbus-daemon", "nano"]

# Logical stage geometry (physical is 2x for crisp captures). Kept
# compact so the staged windows dominate the frame instead of floating
# in empty wallpaper.
W, H = 1600, 900
SCALE = 2


class SessionError(RuntimeError):
    pass


def _tool_env(base: dict) -> dict:
    """PATH/LD_LIBRARY_PATH with DICTATR_DEMO_TOOLS prefixes applied."""
    env = dict(base)
    for prefix in filter(None, os.environ.get(
            "DICTATR_DEMO_TOOLS", "").split(":")):
        p = Path(prefix)
        env["PATH"] = f"{p / 'usr/bin'}:{env.get('PATH', '')}"
        env["LD_LIBRARY_PATH"] = ":".join(
            [str(p / "usr/lib64")]
            + ([env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else []))
    return env


def _pointerd_python(tool_env: dict) -> tuple[str, str]:
    """(python, PYTHONPATH) able to import pywayland."""
    candidates = [(sys.executable, "")]
    for prefix in filter(None, os.environ.get(
            "DICTATR_DEMO_TOOLS", "").split(":")):
        for sp in Path(prefix).glob("usr/lib64/python3.*/site-packages"):
            candidates.append(("/usr/bin/python3", str(sp)))
    candidates.append(("/usr/bin/python3", ""))
    for py, pp in candidates:
        env = dict(tool_env)
        if pp:
            env["PYTHONPATH"] = pp
        r = subprocess.run([py, "-c", "import pywayland"], env=env,
                           capture_output=True)
        if r.returncode == 0:
            return py, pp
    raise SessionError(
        "no python with pywayland found (dnf install python3-pywayland)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Session:
    """Boot and own every process of the demo stage."""

    def __init__(self, keep: bool = False):
        self.keep = keep
        self.procs: list[tuple[str, subprocess.Popen]] = []
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        self.run = runtime / "dictatr-demo"
        self.gen = self.run / "gen"
        self.cues_path = self.run / "cues.jsonl"
        self.env = _tool_env({
            k: v for k, v in os.environ.items()
            if k not in ("WAYLAND_DISPLAY", "SWAYSOCK", "DISPLAY",
                         "LD_PRELOAD", "DBUS_SESSION_BUS_ADDRESS")})
        self.bus_address = None
        self.wayland_display = None   # absolute socket path
        self.swaysock = None
        self.pointerd = None
        self.lemonade_port = None

    # -- lifecycle ------------------------------------------------------
    def start(self, scenario: dict | None = None):
        self._check_tools()
        if self.run.exists():
            shutil.rmtree(self.run)
        for d in (self.gen, self.run / "bin", self.run / "xdg-run",
                  self.run / "archive"):
            d.mkdir(parents=True)
        (self.run / "xdg-run").chmod(0o700)
        self.cues_path.touch()
        try:
            self._write_ydotool_shim()
            self._start_dbus()
            self._render_wallpaper()
            self._start_sway()
            self._start_mako()
            self._start_pointerd()
            self._start_stub(scenario or {})
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        for name, p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(3)
                except subprocess.TimeoutExpired:
                    p.kill()
        if not self.keep and self.run.exists():
            shutil.rmtree(self.run, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()

    def _spawn(self, name, cmd, env, **kw) -> subprocess.Popen:
        p = subprocess.Popen(cmd, env=env, **kw)
        self.procs.append((name, p))
        return p

    def _check_tools(self):
        missing = [t for t in REQUIRED
                   if not shutil.which(t, path=self.env["PATH"])]
        if missing:
            raise SessionError(
                f"missing tools: {' '.join(missing)}\n"
                "  sudo dnf install sway grim wf-recorder wtype mako foot "
                "librsvg2-tools ffmpeg python3-pywayland nano")
        if not (REPO / ".venv/bin/python").exists():
            raise SessionError("repo .venv missing — run install.sh first")

    # -- pieces ---------------------------------------------------------
    def _write_ydotool_shim(self):
        shim = self.run / "bin" / "ydotool"
        shim.write_text(f"""#!/usr/bin/env python3
# Demo shim: dictatr types via `ydotool type -- TEXT`. Inside the nested
# compositor ydotool's uinput events would land on the HOST desktop, so
# forward to wtype (virtual-keyboard protocol) with a typewriter cadence,
# and log cues for the camera.
import json, os, subprocess, sys, time
args = sys.argv[1:]
for tok in ("type", "--"):
    if args and args[0] == tok:
        args.pop(0)
text = " ".join(args)
cues = os.environ.get("DEMO_CUES")
def cue(event, **kw):
    if cues:
        with open(cues, "a") as f:
            f.write(json.dumps({{"t": time.time(), "event": event, **kw}}) + "\\n")
cue("type_start", chars=len(text))
if os.environ.get("DEMO_SWALLOW_TYPE") == "1":
    # The scene already streamed this text "live" while the voice
    # spoke; swallow the app's end-of-utterance delivery so it isn't
    # typed twice. Cues and exit code stay intact.
    rc = 0
else:
    delay = os.environ.get("DEMO_TYPE_CHAR_MS", "24")
    # -s 200: let the virtual keymap settle, or the first char is eaten
    rc = subprocess.run(
        ["wtype", "-s", "200", "-d", delay, "--", text]).returncode
cue("type_end")
sys.exit(rc)
""")
        shim.chmod(0o755)

    def _start_dbus(self):
        # A bare session bus with NO activatable services: without this,
        # GTK's portal lookups would spawn the host's xdg-desktop-portal
        # stack onto the demo bus and block window mapping for seconds.
        conf = self.gen / "dbus.conf"
        conf.write_text("""<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
""")
        p = self._spawn(
            "dbus", ["dbus-daemon", f"--config-file={conf}",
                     "--nofork", "--print-address"],
            self.env, stdout=subprocess.PIPE, text=True)
        line = p.stdout.readline().strip()
        if not line:
            raise SessionError("dbus-daemon failed to start")
        self.bus_address = line
        self.env["DBUS_SESSION_BUS_ADDRESS"] = line

    def _render_wallpaper(self):
        subprocess.run(
            ["rsvg-convert", "-w", str(W * SCALE), "-h", str(H * SCALE),
             str(STAGE / "wallpaper.svg"),
             "-o", str(self.gen / "wallpaper.png")],
            env=self.env, check=True)

    def _start_sway(self):
        envfile = self.gen / "sway-env"
        cfg = string.Template((STAGE / "sway.config").read_text()).substitute(
            WALLPAPER=str(self.gen / "wallpaper.png"),
            ENVFILE=str(envfile),
            PHYS_W=W * SCALE, PHYS_H=H * SCALE, SCALE=SCALE,
        )
        cfg_path = self.gen / "sway.config"
        cfg_path.write_text(cfg)
        env = dict(self.env)
        env.update(
            WLR_BACKENDS="headless",
            WLR_LIBINPUT_NO_DEVICES="1",
            WLR_RENDERER="pixman",
            XDG_CONFIG_HOME=str(STAGE / "xdg"),
            XCURSOR_THEME="Adwaita", XCURSOR_SIZE="32",
        )
        self._spawn("sway", ["sway", "-c", str(cfg_path)], env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 15
        while time.time() < deadline:
            if envfile.exists() and len(envfile.read_text().splitlines()) >= 2:
                break
            time.sleep(0.1)
        else:
            raise SessionError("sway did not come up (no env file)")
        wl, sock = envfile.read_text().splitlines()[:2]
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        self.wayland_display = str(Path(runtime) / wl)
        self.swaysock = sock
        self.env["SWAYSOCK"] = sock
        # grim/wf-recorder/wtype run from this env; absolute path so
        # clients with a different XDG_RUNTIME_DIR still find the socket
        self.env["WAYLAND_DISPLAY"] = self.wayland_display

    def _start_mako(self):
        env = dict(self.env)
        env.update(WAYLAND_DISPLAY=self.wayland_display,
                   XDG_CONFIG_HOME=str(STAGE / "xdg"))
        self._spawn("mako", ["mako"], env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _start_pointerd(self):
        proto = self.gen / "proto"
        py, pypath = _pointerd_python(self.env)
        env = dict(self.env)
        if pypath:
            env["PYTHONPATH"] = pypath
        subprocess.run(
            [py, "-m", "pywayland.scanner",
             "-i", str(DEMO / "protocols/wayland.xml"),
             str(DEMO / "protocols/wlr-virtual-pointer-unstable-v1.xml"),
             "-o", str(proto)],
            env=env, check=True, capture_output=True)
        (proto / "__init__.py").touch()
        env["WAYLAND_DISPLAY"] = self.wayland_display
        self.pointerd = self._spawn(
            "pointerd", [py, str(DEMO / "lib/pointerd.py"), str(self.gen),
                         str(W), str(H)],
            env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        if self.pointerd.stdout.readline().strip() != "ready":
            raise SessionError("pointerd failed (see stderr)")

    def _start_stub(self, scenario: dict):
        self.lemonade_port = _free_port()
        ws_port = _free_port()
        scen_path = self.gen / "scenario.json"
        scen_path.write_text(json.dumps(scenario))
        p = self._spawn(
            "stub", [str(REPO / ".venv/bin/python"),
                     str(DEMO / "lib/stub_lemonade.py"),
                     "--http-port", str(self.lemonade_port),
                     "--ws-port", str(ws_port),
                     "--scenario", str(scen_path),
                     "--cues", str(self.cues_path)],
            self.env, stdout=subprocess.PIPE, text=True)
        if p.stdout.readline().strip() != "ready":
            raise SessionError("lemonade stub failed to start")

    # -- for scenes -----------------------------------------------------
    def app_env(self, **extra) -> dict:
        """Environment for dictatr processes living on the demo stage."""
        env = dict(self.env)
        env.update(
            XDG_RUNTIME_DIR=str(self.run / "xdg-run"),
            WAYLAND_DISPLAY=self.wayland_display,
            XDG_CONFIG_HOME=str(STAGE / "xdg"),
            LEMONADE_URL=f"http://127.0.0.1:{self.lemonade_port}/api/v1",
            DICTATE_ARCHIVE=str(self.run / "archive"),
            # shim first; /usr/bin early so `python3` is the system one
            # (PyGObject for the menu/tray lives in system site-packages)
            PATH=f"{self.run / 'bin'}:/usr/bin:{self.env['PATH']}",
            DEMO_CUES=str(self.cues_path),
            # No portal tier on the stage: typing must go through the
            # ydotool shim above, and the tray must not touch the host's
            # xdg-desktop-portal from the demo bus.
            DICTATE_NO_PORTAL="1",
            # The stage config has no setup_done key, so the tray would
            # otherwise open the wizard three seconds in and land it on
            # top of whatever is being captured. Scenes that want the
            # wizard launch it themselves.
            DICTATE_NO_SETUP="1",
            GTK_THEME="Adwaita:dark",
            GSK_RENDERER="cairo",
            GTK_A11Y="none",
            GTK_USE_PORTAL="0",
            NO_AT_BRIDGE="1",
        )
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def start_tray(self):
        self._spawn("tray", [str(REPO / "bin/dictate-tray")], self.app_env(),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
