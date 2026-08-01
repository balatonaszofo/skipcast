"""ffmpeg wrappers. All audio operations go through subprocess, never a
Python audio library — ffmpeg is the one dependency that reliably handles
every mangled MP3 a podcast host will hand us.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class FFmpegMissing(RuntimeError):
    pass


class FFmpegFailed(RuntimeError):
    pass


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise FFmpegMissing(
            f"{' and '.join(missing)} not found on PATH. Install with: brew install ffmpeg"
        )


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegFailed(f"{cmd[0]} failed ({proc.returncode}):\n{tail}")
    return proc


def probe(path: Path) -> dict:
    """Return the ffprobe format block for a media file."""
    proc = _run(
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-select_streams", "a:0",
            str(path),
        ]
    )
    return json.loads(proc.stdout)


def duration_seconds(path: Path) -> float:
    info = probe(path)
    fmt = info.get("format", {})
    if "duration" in fmt:
        return float(fmt["duration"])
    streams = info.get("streams", [])
    if streams and "duration" in streams[0]:
        return float(streams[0]["duration"])
    raise FFmpegFailed(f"could not determine duration of {path}")


def to_wav(src: Path, dest: Path, sample_rate: int = 16000) -> Path:
    """Decode to mono PCM WAV at the diarizer's working rate.

    Diarization never touches the source MP3 directly: decoding once up front
    keeps the timestamps we produce anchored to real seconds rather than to
    whatever frame layout the encoder happened to emit.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i", str(src),
            "-vn",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-c:a", "pcm_s16le",
            str(dest),
        ]
    )
    return dest


def to_mp3(src: Path, dest: Path, bitrate: str = "128k", channels: int = 1) -> Path:
    """Transcode to the canonical serving format.

    YouTube hands back Opus in a WebM container, which Safari will not play in
    an <audio> element. Everything downstream — the preview page and, later,
    the generated feed — assumes MP3, so normalise once at ingest.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i", str(src),
            "-vn",
            "-ac", str(channels),
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            str(dest),
        ]
    )
    return dest
