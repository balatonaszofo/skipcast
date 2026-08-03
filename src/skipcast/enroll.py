"""Teaching skipcast a voice from a sample, rather than from an episode.

Labelling is the normal way a voice gets learned: process an episode, hear the
clusters, name them. That works but it is backwards for tracking someone —
you cannot follow a person across shows until they have already turned up in
one you subscribe to and sat through the pipeline.

This takes a clip instead. Drop in sixty seconds of someone talking and they
are known from then on, including in the next episode polled.

The embedding comes from the same diarization pipeline the episodes use, run
over the clip. That is the point: a profile is only comparable to other
profiles when it comes out of the same model, so this deliberately does not
reach for a lighter-weight embedder.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import audio, db
from .config import Config

# Below this there is not enough speech for a stable embedding — the vector
# starts describing the room rather than the person.
MIN_SECONDS = 10.0
COMFORTABLE_SECONDS = 25.0

# A clip is supposed to be one person. Another voice under this share of the
# speech is treated as a stray interjection; above it, the clip is rejected,
# because an embedding blended across two people matches neither.
STRAY_SHARE = 0.12


class EnrollError(RuntimeError):
    pass


@dataclass
class Enrolled:
    name: str
    seconds: float
    source: str
    clusters: int
    similarity_to_existing: float = 0.0
    matched_existing: str | None = None


def enroll_clip(conn, cfg: Config, name: str, path: Path,
                start: float = 0.0, end: float | None = None) -> Enrolled:
    """Store a voice profile for `name` from a clip of them speaking."""
    from .diarize import diarize_file

    name = (name or "").strip()
    if not name:
        raise EnrollError("a name is required")

    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise EnrollError(f"no such file: {path}")

    audio.require_ffmpeg()
    full = audio.duration_seconds(path)
    if end is not None and end > full:
        end = full
    span = (end if end is not None else full) - start
    if span < MIN_SECONDS:
        raise EnrollError(
            f"that clip is {span:.1f}s. Voice profiles need at least "
            f"{MIN_SECONDS:.0f}s of speech, and {COMFORTABLE_SECONDS:.0f}s is "
            "where they get reliable."
        )

    with tempfile.TemporaryDirectory(prefix="skipcast-enroll-") as tmp:
        clip = Path(tmp) / "clip.wav"
        if start or end is not None:
            audio.excerpt(path, clip, start, end)
        else:
            clip = path

        print(f"[enroll] embedding {span:.0f}s of audio for {name}",
              file=sys.stderr)
        doc = diarize_file(clip, cfg, {"title": f"enrolment: {name}"})

    speakers = [s for s in doc["speakers"] if s["total_seconds"] > 0]
    if not speakers:
        raise EnrollError("no speech found in that clip")
    speakers.sort(key=lambda s: s["total_seconds"], reverse=True)

    speech = sum(s["total_seconds"] for s in speakers)
    dominant = speakers[0]
    others = [s for s in speakers[1:] if s["total_seconds"] / speech > STRAY_SHARE]
    if others:
        shares = ", ".join(
            f"{s['total_seconds']:.0f}s" for s in [dominant, *others]
        )
        raise EnrollError(
            f"that clip has {len(others) + 1} voices in it ({shares}). Trim it to "
            "one person — --start and --end take seconds — because an embedding "
            "blended across two people matches neither of them."
        )

    if not dominant.get("embedding"):
        raise EnrollError(
            "the clip has too little clean speech to embed. Music, crosstalk "
            "and heavy compression all do this; try a different passage."
        )

    # Say whether this voice is already known, under this name or another one.
    # Enrolling someone twice is fine and even useful — profiles are kept per
    # source — but silently creating a second speaker for a voice that already
    # has one is how a library ends up matching nothing well.
    from . import identity

    existing = db.all_profiles(conn)
    match = identity.match_cluster(
        dominant["embedding"], existing, cfg.identity.match_threshold
    ) if existing else None

    speaker_id = db.get_or_create_speaker(conn, name)
    source = f"enroll:{path.name}"
    if start or end is not None:
        source += f"@{start:.0f}-{end if end is not None else full:.0f}"
    db.add_profile(conn, speaker_id, source, dominant["speaker_label"],
                   dominant["embedding"], dominant["total_seconds"])

    return Enrolled(
        name=name,
        seconds=round(dominant["total_seconds"], 1),
        source=source,
        clusters=len(speakers),
        similarity_to_existing=round(match.similarity, 3) if match else 0.0,
        matched_existing=match.closest_name if match else None,
    )
