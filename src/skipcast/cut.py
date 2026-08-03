"""Phase 2 — turning a segments document into an edited audio file.

Two halves, deliberately separated: build_plan() is pure arithmetic over
timestamps and can be tested without touching audio, and render() is the only
part that runs ffmpeg.

The selection rules, in order:
  1. merge adjacent same-speaker segments separated by less than merge_gap
  2. cut a merged region only if it is at least min_skip_seconds long
  3. pull each cut boundary inward by boundary_padding so the cut does not
     clip the first or last syllable of the speaker being kept
  4. union what remains (speakers overlap during crosstalk)
  5. refuse the whole episode if more than max_skip_fraction would go

Order matters. Diarization emits fragments, not turns — a single monologue
arrives split at every breath — so merging has to happen before the minimum
length test, or the test sees crumbs and rejects almost everything.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import audio
from .config import Config


class CutRefused(RuntimeError):
    """The plan would remove too much. Better to emit nothing than garbage."""


@dataclass
class Region:
    start: float
    end: float
    speaker_label: str

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Decision:
    """One merged region and what we chose to do with it."""
    speaker_label: str
    matched_name: str | None
    similarity: float
    start: float
    end: float
    duration: float
    decision: str   # "cut" | "kept"
    reason: str


@dataclass
class Plan:
    cuts: list[Region] = field(default_factory=list)
    keeps: list[Region] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    duration: float = 0.0
    cut_seconds: float = 0.0
    result_seconds: float = 0.0
    skipped_labels: list[str] = field(default_factory=list)
    absorbed_slivers: int = 0
    dropped_keeps: int = 0

    @property
    def fraction(self) -> float:
        return self.cut_seconds / self.duration if self.duration else 0.0


def merge_same_speaker(segments: list[dict], label: str, gap: float) -> list[Region]:
    """Glue a speaker's fragments back into turns."""
    mine = sorted(
        (s for s in segments if s["speaker_label"] == label),
        key=lambda s: (s["start"], s["end"]),
    )
    out: list[Region] = []
    for s in mine:
        if out and s["start"] - out[-1].end < gap:
            out[-1].end = max(out[-1].end, s["end"])
        else:
            out.append(Region(s["start"], s["end"], label))
    return out


def union(regions: list[Region]) -> list[Region]:
    """Flatten overlapping regions from different speakers into one timeline."""
    merged: list[Region] = []
    for r in sorted(regions, key=lambda r: r.start):
        if merged and r.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, r.end)
            if r.speaker_label not in merged[-1].speaker_label.split("+"):
                merged[-1].speaker_label += "+" + r.speaker_label
        else:
            merged.append(Region(r.start, r.end, r.speaker_label))
    return merged


def resolve_skip_labels(doc: dict, overrides: list[str] | None = None) -> list[str]:
    """Which cluster labels to remove.

    Without overrides this is whatever Phase 1 matched to a speaker carrying
    the skip flag. Overrides accept either a cluster label (SPEAKER_06) or a
    matched name ("Jason Calacanis").
    """
    if overrides:
        wanted = {o.strip().casefold() for o in overrides}
        return [
            s["speaker_label"]
            for s in doc["speakers"]
            if s["speaker_label"].casefold() in wanted
            or (s.get("matched_name") or "").casefold() in wanted
        ]
    return [s["speaker_label"] for s in doc["speakers"] if s.get("skip")]


def build_plan(doc: dict, cfg: Config, overrides: list[str] | None = None) -> Plan:
    c = cfg.cut
    duration = float(doc["duration"])
    labels = resolve_skip_labels(doc, overrides)
    by_label = {s["speaker_label"]: s for s in doc["speakers"]}

    plan = Plan(duration=duration, skipped_labels=labels)
    if not labels:
        plan.keeps = [Region(0.0, duration, "*")]
        plan.result_seconds = duration
        return plan

    candidates: list[Region] = []
    for label in labels:
        spk = by_label.get(label, {})
        for region in merge_same_speaker(doc["segments"], label, c.merge_gap_seconds):
            keep_it = region.duration < c.min_skip_seconds
            plan.decisions.append(Decision(
                speaker_label=label,
                matched_name=spk.get("matched_name"),
                similarity=float(spk.get("similarity") or 0.0),
                start=round(region.start, 3),
                end=round(region.end, 3),
                duration=round(region.duration, 3),
                decision="kept" if keep_it else "cut",
                reason=(f"shorter than min_skip_seconds ({c.min_skip_seconds}s)"
                        if keep_it else "target speaker, long enough to cut"),
            ))
            if not keep_it:
                candidates.append(region)

    # Pull boundaries inward so we do not clip the kept speaker either side.
    # At the very start and end of the episode there is no adjacent kept
    # speaker to protect, so padding there would only strand a fraction of a
    # second of the removed voice against the file boundary.
    # The threshold is the crossfade length, matching the rule further down
    # that drops kept pieces too short to fade: if padding would leave a head
    # or tail fragment that thin, it is unrenderable anyway.
    pad = c.boundary_padding_seconds
    edge = c.crossfade_seconds
    padded = []
    for r in candidates:
        start = 0.0 if r.start < edge else r.start + pad
        end = duration if r.end > duration - edge else r.end - pad
        if end > start:
            padded.append(Region(start, end, r.speaker_label))

    cuts = union(padded)

    # Two cut regions back to back are separated only by their own padding.
    # That padding exists to protect a kept speaker, and between two cuts there
    # is no kept speaker — leaving it behind stitches half a second of the very
    # voice being removed between two jumps. Absorb it.
    if c.merge_adjacent_cuts and cuts:
        absorbed = [cuts[0]]
        for r in cuts[1:]:
            if r.start - absorbed[-1].end <= 2 * c.boundary_padding_seconds:
                absorbed[-1].end = r.end
                plan.absorbed_slivers += 1
            else:
                absorbed.append(r)
        cuts = absorbed

    plan.cuts = cuts
    plan.cut_seconds = sum(r.duration for r in cuts)

    if plan.fraction > c.max_skip_fraction:
        raise CutRefused(
            f"the rules want to remove {plan.cut_seconds / 60:.1f} min of a "
            f"{duration / 60:.1f} min episode ({plan.fraction * 100:.1f}%), over the "
            f"max_skip_fraction ceiling of {c.max_skip_fraction * 100:.0f}%. "
            "Something is wrong — refusing rather than emitting garbage. "
            "Check which speakers are flagged skip, and their match similarity."
        )

    # Keeps are the complement of the cuts.
    keeps: list[Region] = []
    cursor = 0.0
    for r in cuts:
        if r.start > cursor:
            keeps.append(Region(cursor, r.start, "*"))
        cursor = max(cursor, r.end)
    if cursor < duration:
        keeps.append(Region(cursor, duration, "*"))

    # A keep shorter than the crossfade cannot be crossfaded into its
    # neighbours; it would be consumed entirely by the fade. Drop it.
    floor = c.crossfade_seconds
    plan.dropped_keeps = sum(1 for k in keeps if k.duration < floor)
    plan.keeps = [k for k in keeps if k.duration >= floor]
    plan.result_seconds = sum(k.duration for k in plan.keeps)
    return plan


# ---------------------------------------------------------------------------
def _filter_graph(plan: Plan, crossfade: float) -> str:
    """atrim each kept piece, then chain acrossfade between them.

    acrossfade consumes `crossfade` seconds from the tail of the left input and
    the head of the right, so the output is shorter than the sum of the pieces
    by crossfade x (joins). That is expected and accounted for when reporting.
    """
    parts = []
    for i, k in enumerate(plan.keeps):
        parts.append(
            f"[0:a]atrim=start={k.start:.4f}:end={k.end:.4f},"
            f"asetpts=PTS-STARTPTS[p{i}]"
        )
    if len(plan.keeps) == 1:
        parts.append("[p0]anull[out]")
        return ";".join(parts)

    prev = "p0"
    for i in range(1, len(plan.keeps)):
        label = "out" if i == len(plan.keeps) - 1 else f"a{i}"
        parts.append(
            f"[{prev}][p{i}]acrossfade=d={crossfade}:c1=tri:c2=tri[{label}]"
        )
        prev = label
    return ";".join(parts)


def render(src: Path, plan: Plan, cfg: Config, dest: Path) -> Path:
    """Decode to WAV, cut, re-encode.

    Deliberately not a stream copy. Cutting MP3 without re-encoding has to land
    on frame boundaries, which do not line up with speech boundaries, and it
    drags along encoder delay and gapless padding that shift every timestamp
    after the first join. Re-encoding costs one generation of loss that is
    inaudible on speech. Copy-codec cutting is a later optimisation, and would
    need frame-accurate boundary snapping to be worth attempting.
    """
    audio.require_ffmpeg()
    if not plan.keeps:
        raise CutRefused("nothing would be left after cutting")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skipcast-cut-") as tmp:
        wav = _decode(src, Path(tmp) / "decoded.wav")

        graph = _filter_graph(plan, cfg.cut.crossfade_seconds)
        graph_file = Path(tmp) / "graph.txt"
        # The graph runs to tens of kilobytes on a heavily cut episode, past
        # what a command line will take. ffmpeg reads it from a file instead.
        graph_file.write_text(graph)

        cmd = [
            "ffmpeg", "-nostdin", "-y",
            "-i", str(wav),
            "-filter_complex_script", str(graph_file),
            "-map", "[out]",
            "-ac", str(cfg.encode.channels),
            "-c:a", "libmp3lame",
            "-b:a", cfg.encode.bitrate,
            str(dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
            raise audio.FFmpegFailed(f"cut failed:\n{tail}")
    return dest


def _decode(src: Path, dest: Path) -> Path:
    """Decode to PCM at the source rate, preserving channel count for now."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn",
         "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True, check=True,
    )
    return dest


def write_log(plan: Plan, doc: dict, cfg: Config, dest: Path) -> Path:
    """Per-episode record of every cut decision, for debugging bad output."""
    c = cfg.cut
    payload = {
        "schema": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "audio_file": doc.get("audio_file"),
        "title": doc.get("title"),
        "source_url": doc.get("source_url"),
        "config": {
            "merge_gap_seconds": c.merge_gap_seconds,
            "min_skip_seconds": c.min_skip_seconds,
            "boundary_padding_seconds": c.boundary_padding_seconds,
            "crossfade_seconds": c.crossfade_seconds,
            "max_skip_fraction": c.max_skip_fraction,
            "merge_adjacent_cuts": c.merge_adjacent_cuts,
        },
        "skipped_labels": plan.skipped_labels,
        "speakers": [
            {k: s.get(k) for k in
             ("speaker_label", "matched_name", "similarity", "skip",
              "total_seconds", "segment_count")}
            for s in doc["speakers"]
        ],
        "summary": {
            "original_seconds": round(plan.duration, 3),
            "cut_seconds": round(plan.cut_seconds, 3),
            "result_seconds": round(plan.result_seconds, 3),
            "cut_fraction": round(plan.fraction, 4),
            "cut_regions": len(plan.cuts),
            "kept_regions": len(plan.keeps),
            "joins": max(0, len(plan.keeps) - 1),
            "absorbed_padding_slivers": plan.absorbed_slivers,
            "dropped_short_keeps": plan.dropped_keeps,
        },
        "cuts": [asdict(r) for r in plan.cuts],
        # Recorded rather than left to be derived: timeline.py maps original
        # timestamps onto the edit from this, and reconstructing the complement
        # means re-deriving the dropped-short-keep rule from the config.
        "keeps": [asdict(r) for r in plan.keeps],
        "decisions": [asdict(d) for d in plan.decisions],
    }
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def describe(plan: Plan) -> str:
    kept_pct = 100 * plan.result_seconds / plan.duration if plan.duration else 0
    lines = [
        f"  cut {len(plan.cuts)} regions, {plan.cut_seconds / 60:.1f} min "
        f"({plan.fraction * 100:.1f}% of the episode)",
        f"  keeping {len(plan.keeps)} pieces, {plan.result_seconds / 60:.1f} min "
        f"({kept_pct:.1f}%), {max(0, len(plan.keeps) - 1)} crossfaded joins",
    ]
    held = [d for d in plan.decisions if d.decision == "kept"]
    if held:
        secs = sum(d.duration for d in held)
        lines.append(
            f"  left in: {len(held)} short interjections totalling {secs / 60:.1f} min"
        )
    if plan.absorbed_slivers:
        lines.append(f"  absorbed {plan.absorbed_slivers} padding sliver(s) between adjacent cuts")
    if plan.dropped_keeps:
        lines.append(f"  dropped {plan.dropped_keeps} kept piece(s) shorter than the crossfade")
    return "\n".join(lines)
