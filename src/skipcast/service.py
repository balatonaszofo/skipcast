"""launchd agent so the server survives logout, reboot and its own crashes.

Only one agent is installed, running `skipcast serve`. The polling schedule
lives inside that process (see jobs.Scheduler) rather than in a second launchd
job: two processes diarizing at once would contend for the GPU and for SQLite,
and the in-process route means scheduled polls appear in the control panel's
Activity tab exactly like the ones you trigger by hand.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from .config import Config

LABEL = "com.skipcast.server"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _executable() -> Path:
    """The skipcast entry point inside this project's venv.

    sys.argv[0] is right when invoked normally; fall back to the venv layout so
    this still works under `python -m skipcast`.
    """
    candidate = Path(sys.argv[0]).resolve()
    if candidate.is_file() and candidate.name == "skipcast":
        return candidate
    # sys.prefix is the venv root; sys.executable resolves through the symlink
    # to the real interpreter, whose bin/ has no console scripts in it.
    for guess in (Path(sys.prefix) / "bin" / "skipcast",
                  Path(sys.executable).parent / "skipcast"):
        if guess.is_file():
            return guess
    raise FileNotFoundError(
        "cannot locate the skipcast executable; run this from the project's venv"
    )


def build_plist(cfg: Config) -> dict:
    exe = _executable()
    project = cfg.source_path.parent if cfg.source_path else Path.cwd()
    logs = cfg.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    return {
        "Label": LABEL,
        "ProgramArguments": [str(exe), "serve"],
        "WorkingDirectory": str(project),
        "EnvironmentVariables": {
            # launchd starts with a minimal PATH; ffmpeg lives in Homebrew's.
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            # Keep the Apple GPU usable for the ops pyannote cannot run there.
            "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(logs / "skipcast.log"),
        "StandardErrorPath": str(logs / "skipcast.err"),
        # Deliberately no ProcessType: "Background" tells launchd to throttle
        # CPU and I/O, which would stretch a 15-minute diarization badly.
    }


def install(cfg: Config) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = build_plist(cfg)
    with path.open("wb") as fh:
        plistlib.dump(data, fh)

    # bootout first so reinstalling picks up changes rather than erroring.
    uid = _uid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True, text=True)
    proc = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"launchctl bootstrap failed ({proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    subprocess.run(["launchctl", "enable", f"gui/{uid}/{LABEL}"],
                   capture_output=True, text=True)
    return path


def uninstall() -> bool:
    uid = _uid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True, text=True)
    path = plist_path()
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed


def status() -> dict:
    uid = _uid()
    proc = subprocess.run(["launchctl", "print", f"gui/{uid}/{LABEL}"],
                          capture_output=True, text=True)
    installed = plist_path().is_file()
    loaded = proc.returncode == 0
    pid = state = None
    if loaded:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                pid = line.split("=", 1)[1].strip()
            elif line.startswith("state = "):
                state = line.split("=", 1)[1].strip()
    return {"installed": installed, "loaded": loaded, "pid": pid, "state": state,
            "plist": str(plist_path())}


def _uid() -> int:
    import os

    return os.getuid()
