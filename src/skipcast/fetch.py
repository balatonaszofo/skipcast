"""Pull audio from a URL via yt-dlp.

Exists so a YouTube link can be run through the pipeline without hunting for a
podcast MP3 first. The same rule as the rest of skipcast applies: we download
once and keep our own copy, and every timestamp we produce refers to that copy
and nothing else.

The file exactly as downloaded is retained alongside a transcoded MP3 working
copy, so a bad transcode can be redone without hitting the network again.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import audio
from .config import Config


class FetchError(RuntimeError):
    pass


@dataclass
class Fetched:
    audio_path: Path      # transcoded MP3, what everything downstream reads
    original_path: Path   # bytes as delivered, retained
    meta_path: Path
    meta: dict


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def slugify(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:limit].strip("-") or "untitled"


def _progress(status: dict) -> None:
    if status.get("status") != "downloading":
        return
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    done = status.get("downloaded_bytes", 0)
    if total:
        pct = done / total * 100
        # \r keeps it to one line; the caller prints a newline when finished.
        print(f"\r[fetch] {pct:5.1f}%  {done / 1e6:.1f}/{total / 1e6:.1f} MB",
              end="", file=sys.stderr)


def probe_url(url: str, cfg: Config) -> dict:
    """Resolve a URL to its metadata without downloading the media."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover
        raise FetchError("yt-dlp is not installed. Run: uv sync") from exc

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "format": cfg.fetch.format}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 — yt-dlp raises a wide variety
        raise FetchError(f"could not read {url}: {exc}") from exc

    # A playlist URL still resolves; noplaylist means we take the first entry.
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries", []) if e]
        if not entries:
            raise FetchError(f"{url} resolved to an empty playlist")
        info = entries[0]
    return info


def fetch(url: str, cfg: Config, force: bool = False) -> Fetched:
    audio.require_ffmpeg()

    info = probe_url(url, cfg)
    video_id = info.get("id") or slugify(url, 20)
    title = info.get("title") or video_id
    base = f"{slugify(title)}-{video_id}"

    dest_dir = cfg.data_dir / cfg.fetch.source_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = dest_dir / f"{base}.mp3"
    meta_path = dest_dir / f"{base}.meta.json"

    duration = info.get("duration")
    print(
        f"[fetch] {title}"
        + (f" ({duration / 60:.1f} min)" if duration else "")
        + f" — {info.get('uploader') or info.get('channel') or 'unknown'}",
        file=sys.stderr,
    )

    if mp3_path.exists() and meta_path.exists() and not force:
        print(f"[fetch] already have {mp3_path.name}, skipping download", file=sys.stderr)
        meta = json.loads(meta_path.read_text())
        original = Path(meta.get("original_file") or mp3_path)
        return Fetched(mp3_path, original, meta_path, meta)

    from yt_dlp import YoutubeDL

    opts = {
        "format": cfg.fetch.format,
        "outtmpl": {"default": str(dest_dir / f"{base}.source.%(ext)s")},
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,  # we render our own via progress_hooks
        "retries": 3,
        "progress_hooks": [_progress],
        "overwrites": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"download failed for {url}: {exc}") from exc
    finally:
        print(file=sys.stderr)

    if result.get("_type") == "playlist":
        result = [e for e in result.get("entries", []) if e][0]

    downloads = result.get("requested_downloads") or []
    if not downloads or not downloads[0].get("filepath"):
        raise FetchError(f"yt-dlp reported no output file for {url}")
    original = Path(downloads[0]["filepath"])

    print(f"[fetch] transcoding {original.suffix.lstrip('.')} -> mp3", file=sys.stderr)
    audio.to_mp3(original, mp3_path, cfg.encode.bitrate, cfg.encode.channels)

    if not cfg.fetch.keep_original:
        original.unlink(missing_ok=True)

    meta = {
        "schema": 1,
        "source_url": result.get("webpage_url") or url,
        "extractor": result.get("extractor_key"),
        "video_id": video_id,
        "title": title,
        "uploader": result.get("uploader") or result.get("channel"),
        "upload_date": result.get("upload_date"),
        "reported_duration": result.get("duration"),
        # Authoritative: what ffprobe says about the file we actually keep.
        "duration": round(audio.duration_seconds(mp3_path), 3),
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "original_file": str(original) if cfg.fetch.keep_original else None,
        "audio_file": str(mp3_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"[fetch] {mp3_path}", file=sys.stderr)
    return Fetched(mp3_path, original, meta_path, meta)


def load_meta(audio_path: Path) -> dict | None:
    """Metadata for a previously fetched file, if we have any."""
    candidate = audio_path.with_suffix("").with_suffix(".meta.json")
    if candidate.exists():
        return json.loads(candidate.read_text())
    candidate = audio_path.parent / f"{audio_path.stem}.meta.json"
    if candidate.exists():
        return json.loads(candidate.read_text())
    return None
