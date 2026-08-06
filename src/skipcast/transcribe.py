"""Speech-to-text via faster-whisper, attributed to diarized speakers.

The attribution is what makes this worth more than a plain transcript: Phase 0
already knows who spoke when, and Phase 1 knows their names, so the transcript
comes out as "Jason: ..." rather than an undifferentiated wall of text. A
summary built on that can say who argued what.

Whisper's own segment boundaries do not respect speaker changes — one segment
routinely spans a handoff — so words are timed individually and each is
assigned to whichever diarized speaker covers its midpoint. Consecutive words
with the same speaker are then regrouped into turns.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from bisect import bisect_right
from dataclasses import dataclass, asdict
from pathlib import Path

from . import audio
from .config import Config


class TranscriptionError(RuntimeError):
    pass


@dataclass
class Turn:
    start: float
    end: float
    speaker_label: str
    speaker_name: str | None
    text: str


def _speaker_index(doc: dict):
    """Sorted diarization segments plus a lookup for a point in time."""
    segs = sorted(doc["segments"], key=lambda s: s["start"])
    starts = [s["start"] for s in segs]
    names = {
        s["speaker_label"]: s.get("matched_name") for s in doc.get("speakers", [])
    }

    def at(t: float) -> tuple[str | None, str | None]:
        # Rightmost segment starting at or before t; check it actually covers t.
        i = bisect_right(starts, t) - 1
        while i >= 0:
            seg = segs[i]
            if seg["start"] <= t <= seg["end"]:
                label = seg["speaker_label"]
                return label, names.get(label)
            # Overlapping speech means an earlier segment may still cover t.
            if starts[i] < t - 60:
                break
            i -= 1
        return None, None

    return at


def transcribe_file(path: Path, doc: dict, cfg: Config) -> dict:
    """Transcribe audio and attribute each word to a diarized speaker."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise TranscriptionError("faster-whisper is not installed. Run: uv sync") from exc

    audio.require_ffmpeg()
    t = cfg.transcribe
    print(f"[transcribe] loading whisper '{t.model}' ({t.compute_type})", file=sys.stderr)
    # ctranslate2 has no Metal backend, so this is CPU regardless of the GPU.
    model = WhisperModel(t.model, device="cpu", compute_type=t.compute_type)

    with tempfile.TemporaryDirectory(prefix="skipcast-stt-") as tmp:
        wav = audio.to_wav(path, Path(tmp) / "audio.wav", 16000)
        print("[transcribe] transcribing (CPU-bound, several minutes)", file=sys.stderr)
        segments, info = model.transcribe(
            str(wav),
            language=t.language or None,
            beam_size=t.beam_size,
            word_timestamps=True,
            vad_filter=True,
            # distil-whisper's own docs recommend disabling this — the
            # distillation training didn't condition on prior-segment text,
            # and leaving it on tends to produce repetition loops.
            condition_on_previous_text=not t.model.startswith("distil-"),
        )

        at = _speaker_index(doc)
        turns: list[Turn] = []
        words_seen = 0

        for seg in segments:
            for w in (seg.words or []):
                mid = (w.start + w.end) / 2
                label, name = at(mid)
                words_seen += 1
                if turns and turns[-1].speaker_label == (label or "?"):
                    turns[-1].end = w.end
                    turns[-1].text += w.word
                else:
                    turns.append(Turn(w.start, w.end, label or "?", name,
                                      w.word.lstrip()))
            if words_seen and words_seen % 2000 < 50:
                print(f"\r[transcribe] {seg.end / 60:.0f} min transcribed",
                      end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)

    for turn in turns:
        turn.text = turn.text.strip()
    turns = [t_ for t_ in turns if t_.text]

    return {
        "schema": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "audio_file": path.name,
        "model": cfg.transcribe.model,
        "language": getattr(info, "language", None),
        "duration": round(float(getattr(info, "duration", 0) or doc.get("duration", 0)), 2),
        "word_count": words_seen,
        "turns": [asdict(t_) for t_ in turns],
    }


def as_text(transcript: dict, max_chars: int | None = None) -> str:
    """Flatten to speaker-attributed plain text, the form the model reads."""
    lines = []
    for t_ in transcript["turns"]:
        who = t_.get("speaker_name") or t_["speaker_label"]
        stamp = f"[{int(t_['start']) // 60:d}:{int(t_['start']) % 60:02d}]"
        lines.append(f"{stamp} {who}: {t_['text']}")
    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n[transcript truncated]"
    return text


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text())
