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
import math
import sys
import tempfile
from bisect import bisect_right
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import audio
from .config import Config

# 1: turns only. 2: each turn also carries its words with their own timings,
# which is what lets a selection of the text resolve back to a span of audio.
SCHEMA_VERSION = 2


class TranscriptionError(RuntimeError):
    pass


@dataclass
class Turn:
    start: float
    end: float
    speaker_label: str
    speaker_name: str | None
    text: str
    # {"w": word as it appears in text, "s": start, "e": end}. Whisper times
    # every word anyway; keeping them costs a megabyte per episode next to a
    # source file thirty times that size, and throwing them away is what used
    # to make a turn the smallest addressable unit of an episode.
    words: list[dict] = field(default_factory=list)


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
                word = {"w": w.word, "s": round(w.start, 3), "e": round(w.end, 3)}
                if turns and turns[-1].speaker_label == (label or "?"):
                    turns[-1].end = w.end
                    turns[-1].words.append(word)
                else:
                    turns.append(Turn(w.start, w.end, label or "?", name, "", [word]))
            if words_seen and words_seen % 2000 < 50:
                print(f"\r[transcribe] {seg.end / 60:.0f} min transcribed",
                      end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)

    # Derived from the words rather than accumulated alongside them, so the two
    # cannot drift apart — which is what makes a character offset into the text
    # resolvable back to a timestamp.
    for turn in turns:
        turn.text = "".join(x["w"] for x in turn.words).strip()
    turns = [t_ for t_ in turns if t_.text]

    return {
        "schema": SCHEMA_VERSION,
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


def word_spans(turn: dict) -> list[tuple[int, int, float, float]]:
    """Each word as (start char, end char, start second, end second).

    Character positions are relative to `turn["text"]` — the stripped form,
    because that is what gets displayed and therefore what a reader selects
    from. Words that fall entirely inside the stripped whitespace drop out.
    """
    words = turn.get("words") or []
    if not words:
        return []
    raw = "".join(w["w"] for w in words)
    lead = len(raw) - len(raw.lstrip())
    width = len(raw.strip())

    out: list[tuple[int, int, float, float]] = []
    cursor = 0
    for w in words:
        start, cursor = cursor, cursor + len(w["w"])
        a, b = max(0, start - lead), min(width, cursor - lead)
        if b > a:
            out.append((a, b, float(w["s"]), float(w["e"])))
    return out


def time_range(turn: dict, start_char: int = 0,
               end_char: int | None = None) -> tuple[float, float]:
    """When a stretch of a turn's text was spoken.

    Schema-1 transcripts carry no per-word timing, so there the position is
    interpolated across the turn on the assumption of an even speaking rate.
    That lands a boundary within a word or two rather than exactly on it —
    close enough to trim a clip by, and it means an existing library keeps
    working without re-running Whisper over every episode to backfill.
    """
    text = turn.get("text") or ""
    t0, t1 = float(turn["start"]), float(turn["end"])
    end_char = len(text) if end_char is None else end_char
    # Selecting right-to-left is an ordinary gesture, not an empty selection.
    if end_char < start_char:
        start_char, end_char = end_char, start_char
    start_char = max(0, min(start_char, len(text)))
    end_char = max(start_char, min(end_char, len(text)))

    spans = word_spans(turn)
    if not spans:
        if not text:
            return t0, t1
        width = t1 - t0
        return (t0 + width * start_char / len(text),
                t0 + width * end_char / len(text))

    # Any word the selection touches at all is part of it: half a word is not
    # a thing you can play.
    touched = [(s, e) for a, b, s, e in spans if a < end_char and b > start_char]
    if not touched:
        return t0, t1
    return min(s for s, _ in touched), max(e for _, e in touched)


def char_range(turn: dict, t0: float, t1: float) -> tuple[int, int]:
    """The inverse of time_range: which characters were spoken in a window.

    Used to quote only the overlapping part of a turn, so a clip that starts
    mid-monologue does not drag the whole monologue in with it.
    """
    text = turn.get("text") or ""
    spans = word_spans(turn)
    if not spans:
        start, end = float(turn["start"]), float(turn["end"])
        width = end - start
        if width <= 0:
            return 0, len(text)
        lo = max(0, min(int(len(text) * max(0.0, (t0 - start) / width)), len(text)))
        hi = max(0, min(math.ceil(len(text) * min(1.0, (t1 - start) / width)),
                        len(text)))
        # Interpolating by character lands inside a word about as often as not.
        # Widen to whole words, so a quote never opens on "...lling around".
        while lo > 0 and not text[lo - 1].isspace():
            lo -= 1
        while hi < len(text) and not text[hi].isspace():
            hi += 1
        return lo, hi

    touched = [(a, b) for a, b, s, e in spans if s < t1 and e > t0]
    if not touched:
        return 0, 0
    return min(a for a, _ in touched), max(b for _, b in touched)


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text())
