"""Runs a local Ollama server for as long as Filament Forge is running.

Three modes:
    bundled   Download Ollama's portable build into data/ollama and run it as a
              child process. No admin rights, no Docker. Default.
    docker    Run the official ollama/ollama container (`--rm`, so it's removed
              when stopped). Models are kept in a named Docker volume.
    external  Use an Ollama you already run yourself; this app doesn't start
              or stop it.

In bundled mode the server is tied to this app's process: on Windows it's put
in a Job Object that kills it when the app exits (even on a crash or when the
console window is closed). Everywhere it's also stopped on normal exit
(lifespan + atexit), and a leftover from a crashed run is cleaned up at the
next start using the saved PID.
"""

from __future__ import annotations

import atexit
import hashlib
import io
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path

import httpx

RELEASE_URL = "https://github.com/ollama/ollama/releases/latest/download/"
DEFAULT_PORT = 11435  # not 11434, so we never clash with an Ollama you already run
DOCKER_IMAGE = "ollama/ollama"
DOCKER_NAME = "filament-forge-ollama"
DOCKER_VOLUME = "filament-forge-ollama"
DEFAULT_MODEL = "qwen2.5vl:7b"  # reads text and photos; ~6 GB
MIN_FREE_GB = 10

DEFAULT_SETTINGS = {
    "enabled": False,       # user must opt in (big download)
    "mode": "bundled",      # bundled | docker | external
    "gpu": "auto",          # auto | nvidia | amd | cpu
    "model": DEFAULT_MODEL,
    "external_url": "http://127.0.0.1:11434",
    "port": DEFAULT_PORT,
}

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


# --------------------------------------------------------------- helpers
def detect_gpu() -> str:
    """Best-effort guess: 'nvidia', 'amd', or 'cpu'."""
    names = ""
    try:
        if os.name == "nt":
            names = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController).Name"],
                capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW).stdout
        elif sys.platform == "darwin":
            return "cpu"  # Apple GPUs are used automatically by the mac build
        else:
            if shutil.which("nvidia-smi"):
                return "nvidia"
            names = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return "cpu"
    lines = [l.lower() for l in names.splitlines()]
    if os.name != "nt":  # lspci: only look at graphics devices, not AMD chipsets
        lines = [l for l in lines if any(k in l for k in ("vga", "3d controller", "display"))]
    low = "\n".join(lines)
    if "nvidia" in low:
        return "nvidia"
    if "radeon" in low or "amd" in low:
        return "amd"
    return "cpu"


def release_assets(gpu: str) -> list[str]:
    """Which Ollama release files this computer needs, base build first."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if os.name == "nt":
        if arm:
            return ["ollama-windows-arm64.zip"]
        return ["ollama-windows-amd64.zip"] + (["ollama-windows-amd64-rocm.zip"] if gpu == "amd" else [])
    if sys.platform == "darwin":
        return ["ollama-darwin.tgz"]
    if arm:
        return ["ollama-linux-arm64.tar.zst"]
    return ["ollama-linux-amd64.tar.zst"] + (["ollama-linux-amd64-rocm.tar.zst"] if gpu == "amd" else [])


def _extract(archive: Path, dest: Path) -> None:
    name = archive.name
    dest.mkdir(parents=True, exist_ok=True)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            _safe_members(dest, z.namelist())
            z.extractall(dest)
    elif name.endswith((".tgz", ".tar.gz")):
        with tarfile.open(archive, "r:gz") as t:
            _safe_members(dest, t.getnames())
            t.extractall(dest, filter="data")
    elif name.endswith(".tar.zst"):
        try:
            from compression import zstd  # Python 3.14+
            raw = zstd.decompress(archive.read_bytes())
        except ImportError:
            try:
                import zstandard
                with archive.open("rb") as fh:
                    raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
            except ImportError:
                if not shutil.which("tar"):
                    raise RuntimeError("Can't unpack .tar.zst: install the 'zstandard' package")
                subprocess.run(["tar", "--zstd", "-xf", str(archive), "-C", str(dest)], check=True)
                return
        with tarfile.open(fileobj=io.BytesIO(raw)) as t:
            _safe_members(dest, t.getnames())
            t.extractall(dest, filter="data")
    else:
        raise RuntimeError(f"Unknown archive type: {name}")


def _safe_members(dest: Path, names: list[str]) -> None:
    root = dest.resolve()
    for n in names:
        p = (dest / n).resolve()
        if p != root and root not in p.parents:
            raise RuntimeError(f"Unsafe path in archive: {n}")


def find_binary(root: Path) -> Path | None:
    exe = "ollama.exe" if os.name == "nt" else "ollama"
    if not root.is_dir():
        return None
    for p in sorted(root.rglob(exe), key=lambda x: len(x.parts)):
        if p.is_file():
            return p
    return None


# ----------------------------------------------------- process lifetime
class _WinJob:
    """Windows Job Object that kills everything in it when this app exits."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._k32 = k32
        self.handle = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))

    def add(self, proc: subprocess.Popen) -> None:
        self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


# ---------------------------------------------------------------- manager
class OllamaManager:
    """Owns setup, start/stop and model downloads. Thread-safe status."""

    def __init__(self, data_dir: Path, settings: dict | None = None):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "ollama"
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.proc: subprocess.Popen | None = None
        self.job = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        atexit.register(self.stop)
        self.status = {"state": "off", "message": "AI is turned off.", "progress": None,
                       "detail": None, "gpu": None, "binary": None}

    # ---- status
    def _set(self, state: str, message: str, progress: float | None = None, detail: str | None = None):
        with self._lock:
            self.status.update(state=state, message=message, progress=progress, detail=detail)

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self.status)
        s["settings"] = dict(self.settings)
        s["url"] = self.url
        s["installed"] = bool(self.binary()) if self.settings["mode"] == "bundled" else None
        s["running"] = self.is_up()
        s["busy"] = bool(self._worker and self._worker.is_alive())
        return s

    @property
    def url(self) -> str:
        if self.settings["mode"] == "external":
            return self.settings["external_url"].rstrip("/")
        return f"http://127.0.0.1:{int(self.settings['port'])}"

    def is_up(self) -> bool:
        try:
            return httpx.get(self.url + "/api/version", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def binary(self) -> Path | None:
        return find_binary(self.root / "bin")

    def ready(self) -> bool:
        return self.status["state"] == "ready" and self.is_up()

    # ---- public actions (run in background)
    def start_async(self) -> None:
        if self._worker and self._worker.is_alive():
            if not self._cancel.is_set():
                return  # already working on it
            self._worker.join(10)  # let a cancelled run finish first
        self._cancel.clear()
        self._worker = threading.Thread(target=self._run_setup, daemon=True)
        self._worker.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run_setup(self) -> None:
        try:
            mode = self.settings["mode"]
            if mode == "bundled":
                self._ensure_installed()
                self._start_bundled()
            elif mode == "docker":
                self._start_docker()
            else:
                self._set("starting", "Connecting to your Ollama…")
            self._wait_up()
            self._ensure_model()
            self._set("ready", f"Ready: {self.settings['model']}")
        except _Cancelled:
            self._set("off", "Cancelled.")
        except Exception as e:  # report everything to the UI
            self._set("error", str(e))

    # ---- install
    def _ensure_installed(self) -> None:
        if self.binary():
            return
        gpu = self.settings["gpu"]
        if gpu == "auto":
            self._set("checking", "Checking your graphics card…")
            gpu = detect_gpu()
        with self._lock:
            self.status["gpu"] = gpu
        free_gb = shutil.disk_usage(self.data_dir if self.data_dir.exists() else self.data_dir.parent).free / 1e9
        if free_gb < MIN_FREE_GB:
            raise RuntimeError(f"Need about {MIN_FREE_GB} GB free for the AI; only {free_gb:.1f} GB left.")

        dl = self.root / "downloads"
        dl.mkdir(parents=True, exist_ok=True)
        sums = self._checksums()
        for asset in release_assets(gpu):
            path = dl / asset
            self._download(RELEASE_URL + asset, path, f"Downloading AI engine ({asset})")
            want = sums.get(asset)
            if want:
                self._set("verifying", f"Checking {asset}…")
                if _sha256(path) != want:
                    path.unlink(missing_ok=True)
                    raise RuntimeError(f"{asset} failed its checksum. Try again.")
            self._set("extracting", f"Unpacking {asset}…")
            _extract(path, self.root / "bin")
            path.unlink(missing_ok=True)
        exe = self.binary()
        if not exe:
            raise RuntimeError("Downloaded Ollama but couldn't find the program inside it.")
        if os.name != "nt":
            exe.chmod(exe.stat().st_mode | 0o111)

    def _checksums(self) -> dict:
        try:
            r = httpx.get(RELEASE_URL + "sha256sum.txt", follow_redirects=True, timeout=30)
            r.raise_for_status()
        except httpx.HTTPError:
            return {}
        out = {}
        for line in r.text.splitlines():
            parts = line.split()
            if len(parts) == 2:
                out[parts[1].lstrip("*./")] = parts[0].lower()
        return out

    def _download(self, url: str, dest: Path, label: str) -> None:
        part = dest.with_suffix(dest.suffix + ".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with part.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    if self._cancel.is_set():
                        raise _Cancelled()
                    fh.write(chunk)
                    done += len(chunk)
                    self._set("downloading", label, done / total if total else None,
                              f"{done / 1e6:,.0f} / {total / 1e6:,.0f} MB" if total else f"{done / 1e6:,.0f} MB")
        part.replace(dest)

    # ---- run
    def _start_bundled(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if self.is_up():
            return  # left over from a previous run of this app; reuse it
        exe = self.binary()
        self._set("starting", "Starting the AI engine…")
        env = dict(os.environ)
        env["OLLAMA_HOST"] = f"127.0.0.1:{int(self.settings['port'])}"
        env["OLLAMA_MODELS"] = str(self.root / "models")
        log = (self.root / "ollama.log").open("ab")
        kwargs: dict = {"env": env, "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = _NO_WINDOW
        self.proc = subprocess.Popen([str(exe), "serve"], **kwargs)
        if os.name == "nt":
            try:
                self.job = _WinJob()
                self.job.add(self.proc)
            except OSError:
                self.job = None
        (self.root / "ollama.pid").write_text(str(self.proc.pid))

    def _docker(self, *args, check=True, timeout=600) -> subprocess.CompletedProcess:
        if not shutil.which("docker"):
            raise RuntimeError("Docker isn't installed or isn't on PATH. Install Docker Desktop, "
                               "or switch the AI mode to 'bundled'.")
        return subprocess.run(["docker", *args], capture_output=True, text=True, check=check,
                              timeout=timeout, creationflags=_NO_WINDOW)

    def _start_docker(self) -> None:
        if self.is_up():
            return
        self._set("starting", "Starting the Docker container (first time downloads the image)…")
        try:
            self._docker("info", timeout=30)
        except subprocess.CalledProcessError:
            raise RuntimeError("Docker is installed but not running. Start Docker Desktop and try again.")
        self._docker("rm", "-f", DOCKER_NAME, check=False)
        gpu = self.settings["gpu"]
        if gpu == "auto":
            gpu = detect_gpu()
        args = ["run", "-d", "--rm", "--name", DOCKER_NAME,
                "-p", f"127.0.0.1:{int(self.settings['port'])}:11434",
                "-v", f"{DOCKER_VOLUME}:/root/.ollama"]
        if gpu == "nvidia":
            args += ["--gpus=all"]
        try:
            self._docker(*args, DOCKER_IMAGE)
        except subprocess.CalledProcessError as e:
            if "--gpus=all" in args:  # GPU passthrough not available: fall back to CPU
                args.remove("--gpus=all")
                self._docker(*args, DOCKER_IMAGE)
            else:
                raise RuntimeError(f"docker run failed: {e.stderr.strip()[:300]}")

    def _wait_up(self, timeout: float = 90) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self._cancel.is_set():
                raise _Cancelled()
            if self.is_up():
                return
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"The AI engine stopped right away. See {self.root / 'ollama.log'}")
            time.sleep(0.5)
        raise RuntimeError(f"Couldn't reach Ollama at {self.url}.")

    def _ensure_model(self) -> None:
        model = self.settings["model"]
        tags = httpx.get(self.url + "/api/tags", timeout=10).json().get("models", [])
        have = {m.get("name") for m in tags} | {m.get("model") for m in tags}
        if model in have or (":" not in model and f"{model}:latest" in have):
            return
        self._set("pulling", f"Downloading model {model}…", None)
        with httpx.stream("POST", self.url + "/api/pull", json={"model": model, "stream": True},
                          timeout=httpx.Timeout(60, read=600)) as r:
            for line in r.iter_lines():
                if self._cancel.is_set():
                    raise _Cancelled()
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise RuntimeError(f"Model download failed: {msg['error']}")
                total, done = msg.get("total"), msg.get("completed")
                frac = done / total if total and done else None
                detail = f"{(done or 0) / 1e9:.2f} / {total / 1e9:.2f} GB" if total else None
                self._set("pulling", f"Downloading model {model}: {msg.get('status', '')}", frac, detail)

    # ---- stop
    def stop(self) -> None:
        self._cancel.set()
        mode = self.settings["mode"]
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self.job:
            self.job.close()  # kills any runner processes left in the job
            self.job = None
        if mode == "docker" and shutil.which("docker"):
            try:
                self._docker("stop", DOCKER_NAME, check=False, timeout=30)
            except (subprocess.SubprocessError, RuntimeError):
                pass
        (self.root / "ollama.pid").unlink(missing_ok=True)
        if self.status["state"] != "error":
            self._set("off", "AI engine stopped.")

    def cleanup_stale(self) -> None:
        """Kill an Ollama left running by a previous run of this app that crashed."""
        pidfile = self.root / "ollama.pid"
        if not pidfile.exists():
            return
        try:
            pid = int(pidfile.read_text())
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=_NO_WINDOW)
            else:
                os.kill(pid, signal.SIGTERM)
        except (ValueError, OSError):
            pass
        pidfile.unlink(missing_ok=True)

    def uninstall(self) -> None:
        """Remove the downloaded engine and models (frees several GB)."""
        self.stop()
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        self._set("off", "AI files removed.")

    def disk_usage_gb(self) -> float:
        if not self.root.exists():
            return 0.0
        return round(sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()) / 1e9, 2)

    # ---- inference
    def chat(self, messages: list[dict], schema: dict | None = None, timeout: float = 300) -> str:
        body = {"model": self.settings["model"], "messages": messages, "stream": False,
                "options": {"temperature": 0, "num_ctx": 8192}, "keep_alive": "10m"}
        if schema:
            body["format"] = schema
        r = httpx.post(self.url + "/api/chat", json=body, timeout=httpx.Timeout(30, read=timeout))
        if r.status_code != 200:
            raise RuntimeError(f"AI request failed ({r.status_code}): {r.text[:300]}")
        return r.json()["message"]["content"]


class _Cancelled(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
