"""Managed lemond: a private Lemonade instance that dictatr owns.

The embeddable release is a self-contained tarball (the lemond daemon
plus resource catalogs); it runs as a detached child with its own
working dir, port and API key, --no-broadcast. models_dir stays "auto"
(the shared HuggingFace cache) so models are shared with other apps.
"""

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from ..settings import DATA_HOME

# Pinned embeddable release, and the only copy of the pin. The packages
# do not vendor the binary (a prebuilt x86-64 ELF cannot ride inside an
# arch-independent package, and bundling one bars it from Debian and
# Fedora), so this is fetched on first use of the managed backend.
PINNED_VERSION = "11.8.0"
PINNED_SHA256 = \
    "3cb13e93b0496c583e4cb4dda6aef58c39fc71fbb058fb171d62ac18f4cd72fc"
TARBALL_URL = (
    "https://github.com/lemonade-sdk/lemonade/releases/download/"
    f"v{PINNED_VERSION}/lemonade-embeddable-{PINNED_VERSION}-ubuntu-x64.tar.gz"
)

DATA = DATA_HOME  # archive included; see settings.DATA_HOME
VENDORED = Path("/usr/lib/dictatr/lemond/lemond")  # if a distro ships one
DOWNLOADED = DATA / "lemond" / "lemond"            # source installs
STATE = DATA / "lemonade"  # lemond working dir: config.json, engines, log
PORT_FILE = STATE / "dictatr.port"
KEY_FILE = STATE / "dictatr.key"
PID_FILE = STATE / "dictatr.pid"
LOG_FILE = STATE / "lemond.log"


def ensure_binary(on_progress=None) -> Path:
    for p in (VENDORED, DOWNLOADED):
        if p.is_file():
            return p
    return download_lemond(on_progress)


def download_lemond(on_progress=None) -> Path:
    """Fetch the pinned tarball, verify its sha256, extract next to
    DOWNLOADED (top-level dir stripped). on_progress gets a percent."""
    DOWNLOADED.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=DOWNLOADED.parent,
                                     suffix=".tar.gz") as tmp:
        with urllib.request.urlopen(TARBALL_URL, timeout=120) as r:
            total = int(r.headers.get("Content-Length") or 0)
            digest = hashlib.sha256()
            while chunk := r.read(1 << 16):
                digest.update(chunk)
                tmp.write(chunk)
                if on_progress and total:
                    on_progress(tmp.tell() * 100 / total)
        if digest.hexdigest() != PINNED_SHA256:
            raise RuntimeError(
                f"lemond tarball sha256 mismatch: got {digest.hexdigest()}, "
                f"pinned {PINNED_SHA256}")
        tmp.flush()
        with tarfile.open(tmp.name) as tar:
            for m in tar.getmembers():
                parts = Path(m.name).parts
                if len(parts) < 2 or ".." in parts:
                    continue  # skip the top-level dir itself and escapes
                m.name = str(Path(*parts[1:]))
                try:
                    tar.extract(m, DOWNLOADED.parent, filter="data")
                except TypeError:  # filter= needs python >= 3.11.4
                    tar.extract(m, DOWNLOADED.parent)
    for name in ("lemond", "lemonade"):
        binary = DOWNLOADED.parent / name
        if binary.is_file():
            binary.chmod(0o755)
    if not DOWNLOADED.is_file():
        raise RuntimeError("lemond tarball had no lemond binary")
    return DOWNLOADED


def port() -> int:
    """The instance's private port: picked free once, then persisted."""
    try:
        return int(PORT_FILE.read_text())
    except (OSError, ValueError):
        pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
    STATE.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(p))
    return p


def api_key() -> str:
    try:
        key = KEY_FILE.read_text().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(16)
    STATE.mkdir(parents=True, exist_ok=True)
    KEY_FILE.touch(mode=0o600)
    KEY_FILE.chmod(0o600)  # touch honors umask; enforce
    KEY_FILE.write_text(key)
    return key


def root() -> str:
    return f"http://127.0.0.1:{port()}"


def api_base() -> str:
    return f"{root()}/api/v1"


def _headers(key: str | None = None) -> dict:
    return {"Authorization": f"Bearer {key or api_key()}"}


def alive(timeout: float = 1.5) -> bool:
    if not PORT_FILE.exists():
        return False  # never started; don't mint a port just to probe it
    req = urllib.request.Request(f"{root()}/live", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start(wait_s: float = 30.0) -> str:
    """Start (or find already running) the managed instance; returns its
    api_base. The child is detached so it outlives the CLI process."""
    if alive():
        return api_base()
    binary = ensure_binary()
    STATE.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "LEMONADE_API_KEY": api_key()}
    with open(LOG_FILE, "ab") as log:
        proc = subprocess.Popen(
            [str(binary), str(STATE), "--port", str(port()),
             "--host", "127.0.0.1", "--no-broadcast"],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            env=env, start_new_session=True)
    PID_FILE.write_text(str(proc.pid))
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if alive(timeout=1.0):
            return api_base()
        if proc.poll() is not None:
            raise RuntimeError(f"lemond exited at startup (see {LOG_FILE})")
        time.sleep(0.3)
    raise RuntimeError(f"lemond not answering on port {port()} "
                       f"after {wait_s:.0f}s (see {LOG_FILE})")


def stop() -> bool:
    """Clean shutdown over HTTP, SIGTERM fallback via the pid file.
    True when a running instance was told to stop."""
    if alive():
        try:
            req = urllib.request.Request(f"{root()}/internal/shutdown",
                                         data=b"", headers=_headers())
            with urllib.request.urlopen(req, timeout=5):
                pass
            PID_FILE.unlink(missing_ok=True)
            return True
        except Exception:
            pass
    try:
        pid = int(PID_FILE.read_text())
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, ValueError, ProcessLookupError):
        return False
    finally:
        PID_FILE.unlink(missing_ok=True)


def status() -> dict:
    binary = next((p for p in (VENDORED, DOWNLOADED) if p.is_file()), None)
    return {
        "running": alive(),
        "api_base": api_base() if PORT_FILE.exists() else None,
        "binary": str(binary) if binary else None,
        "version": PINNED_VERSION,
        "state_dir": str(STATE),
    }


def _get(path: str, base: str | None = None, key: str | None = None,
         timeout: float = 10.0) -> dict:
    if base is None:
        base, key = api_base(), key or api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    req = urllib.request.Request(base + path, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _post(path: str, payload: dict, base: str | None = None,
          key: str | None = None, timeout: float = 60.0) -> dict:
    if base is None:
        base, key = api_base(), key or api_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + path,
                                 data=json.dumps(payload).encode(),
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def models(base: str | None = None, key: str | None = None) -> list[dict]:
    return _get("/models", base, key).get("data", [])


def system_info(base: str | None = None, key: str | None = None) -> dict:
    return _get("/system-info", base, key)


def pin(model: str, base: str | None = None, key: str | None = None) -> None:
    """Pin *model* against Lemonade's memory eviction (root-level
    /internal endpoint, like /internal/shutdown)."""
    if base is None:
        base, key = api_base(), key or api_key()
    _post("/internal/pin", {"model": model},
          base.split("/api/")[0], key)


def pull(model: str, on_progress=None, base: str | None = None,
         key: str | None = None) -> dict:
    """POST /pull and follow the SSE progress stream. on_progress is
    called with each event dict ({"file", "bytes_downloaded", "percent"});
    returns the last event seen."""
    if base is None:
        base, key = api_base(), key or api_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        f"{base}/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers=headers)
    last: dict = {}
    with urllib.request.urlopen(req) as r:
        for event in _sse_events(r):
            last = event
            if on_progress:
                on_progress(event)
    return last


def _sse_events(stream):
    """Decode `data: {...}` lines; other SSE framing is noise here."""
    for raw in stream:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue
