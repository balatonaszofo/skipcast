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
    # "cut" removes the named speakers. "keep_only" removes everyone else,
    # which is the same arithmetic run against the complement — but a
    # different enough intent that the ceiling and the minimum length have
    # their own settings.
    mode: str = "cut"
    kept_labels: list[str] = field(default_factory=list)
    # How much of cut_seconds came from ad reads and housekeeping rather than
    # from a speaker. Reported separately because they are different promises:
    # one is "you asked for this voice gone", the other "this was not the show".
    interstitial_seconds: float = 0.0

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


def subtract(regions: list[Region], minus: list[Region]) -> list[Region]:
    """The parts of `regions` not already covered by `minus`.

    Used to attribute the cut honestly: an ad read that falls inside a stretch
    of a speaker who was being removed anyway did not shorten the episode by
    its own length, and reporting it as if it did makes the two numbers in the
    feed note add up to more than what went.
    """
    out: list[Region] = []
    blockers = union(minus)
    for r in union(regions):
        cursor = r.start
        for b in blockers:
            if b.end <= cursor or b.start >= r.end:
                continue
            if b.start > cursor:
                out.append(Region(cursor, min(b.start, r.end), r.speaker_label))
            cursor = max(cursor, b.end)
            if cursor >= r.end:
                break
        if cursor < r.end:
            out.append(Region(cursor, r.end, r.speaker_label))
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


def match_labels(doc: dict, names: list[str]) -> list[str]:
    """Cluster labels for a list of names or labels.

    Accepts either a cluster label (SPEAKER_06) or a matched name ("Jason
    Calacanis"), since both are things a caller reasonably has to hand.
    """
    wanted = {o.strip().casefold() for o in names if o and o.strip()}
    return [
        s["speaker_label"]
        for s in doc["speakers"]
        if s["speaker_label"].casefold() in wanted
        or (s.get("matched_name") or "").casefold() in wanted
    ]


def resolve_skip_labels(doc: dict, overrides: list[str] | None = None) -> list[str]:
    """Which cluster labels to remove.

    Without overrides this is whatever Phase 1 matched to a speaker carrying
    the skip flag.
    """
    if overrides:
        return match_labels(doc, overrides)
    return [s["speaker_label"] for s in doc["speakers"] if s.get("skip")]


def _skip_candidates(doc: dict, cfg: Config, labels: list[str], plan: Plan) -> list[Region]:
    """Regions to remove when the named speakers are the ones being cut."""
    c = cfg.cut
    by_label = {s["speaker_label"]: s for s in doc["speakers"]}
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
    return candidates


def _keep_only_candidates(doc: dict, cfg: Config, labels: list[str],
                          plan: Plan, duration: float) -> list[Region]:
    """Regions to remove when the named speakers are the only ones being kept.

    The same arithmetic as the other direction, run against the complement:
    glue the target's fragments back into turns, union them, and everything
    outside that is a candidate for removal.

    The minimum length is its own setting rather than min_skip_seconds. Here it
    governs how much of *everyone else* has to run before it goes, and a
    threshold tuned for "do not cut every mhm" is far too coarse — set at 15s it
    would leave most of an interview in, because a question rarely runs that
    long. Leaving short exchanges in is deliberate: an answer with the question
    removed is a person talking to nobody.
    """
    c = cfg.cut
    by_label = {s["speaker_label"]: s for s in doc["speakers"]}
    mine: list[Region] = []
    for label in labels:
        mine.extend(
            merge_same_speaker(doc["segments"], label, c.merge_gap_seconds)
        )
    kept = union(mine)

    gaps: list[Region] = []
    cursor = 0.0
    for r in kept:
        if r.start > cursor:
            gaps.append(Region(cursor, r.start, "others"))
        cursor = max(cursor, r.end)
    if cursor < duration:
        gaps.append(Region(cursor, duration, "others"))

    minimum = c.keep_only_min_cut_seconds
    candidates = []
    for gap in gaps:
        cut_it = gap.duration >= minimum
        plan.decisions.append(Decision(
            speaker_label="others",
            matched_name=None,
            similarity=0.0,
            start=round(gap.start, 3),
            end=round(gap.end, 3),
            duration=round(gap.duration, 3),
            decision="cut" if cut_it else "kept",
            reason=("everyone but the target, long enough to cut" if cut_it
                    else f"shorter than keep_only_min_cut_seconds ({minimum}s)"),
        ))
        if cut_it:
            candidates.append(gap)

    # Named for the log's benefit; the target speakers are what survives.
    plan.kept_labels = list(labels)
    return candidates


def _extra_regions(extra_cuts, duration: float, plan: Plan) -> list[Region]:
    """Ranges to remove that have nothing to do with who is speaking.

    Ad reads and housekeeping, found in the transcript rather than in the
    diarization. They arrive already filtered for length and confidence, so
    they go straight in as candidates — but they are recorded as decisions like
    everything else, because "why is this gone" has to be answerable from the
    cut log alone.
    """
    out = []
    for item in extra_cuts or []:
        start = float(item.get("from_seconds", 0.0))
        end = min(float(item.get("to_seconds", 0.0)), duration)
        if end <= start:
            continue
        kind = str(item.get("kind") or "interstitial")
        out.append(Region(start, end, kind))
        plan.decisions.append(Decision(
            speaker_label=kind,
            matched_name=None,
            similarity=0.0,
            start=round(start, 3),
            end=round(end, 3),
            duration=round(end - start, 3),
            decision="cut",
            reason=(item.get("what") or kind)
                   + f" ({item.get('confidence', 'unsure')})",
        ))
    return out


def build_plan(doc: dict, cfg: Config, overrides: list[str] | None = None,
               keep_only: list[str] | None = None,
               extra_cuts: list[dict] | None = None) -> Plan:
    """Decide what to remove.

    `keep_only` inverts the question: instead of naming who to cut, name who to
    keep and everyone else goes. That is what a person feed is built from.

    `extra_cuts` are ranges to remove regardless of who is talking — the ad
    reads and housekeeping the transcript turned up. They pass through the same
    padding, union and ceiling rules as everything else, so one set of
    guarantees covers both kinds of removal.
    """
    c = cfg.cut
    duration = float(doc["duration"])
    mode = "keep_only" if keep_only else "cut"
    labels = (match_labels(doc, keep_only) if keep_only
              else resolve_skip_labels(doc, overrides))

    plan = Plan(duration=duration, mode=mode)
    if mode == "cut" and not labels and extra_cuts:
        # Nothing to cut speaker-wise, but there are ads to remove. Without
        # this the early return below would ship the episode unedited.
        plan.skipped_labels = []
        return _finish_plan(plan, [], _extra_regions(extra_cuts, duration, plan),
                            cfg, duration)
    if mode == "keep_only":
        if not labels:
            # Nothing to keep is not "keep everything" — it means the voice we
            # were asked for is not in this episode, and an unfiltered episode
            # served into a person feed would be a silent lie about what it is.
            raise CutRefused(
                "none of the speakers to keep appear in this episode: "
                + ", ".join(keep_only)
            )
        candidates = _keep_only_candidates(doc, cfg, labels, plan, duration)
    else:
        plan.skipped_labels = labels
        if not labels:
            plan.keeps = [Region(0.0, duration, "*")]
            plan.result_seconds = duration
            return plan
        candidates = _skip_candidates(doc, cfg, labels, plan)

    return _finish_plan(plan, candidates,
                        _extra_regions(extra_cuts, duration, plan),
                        cfg, duration)


def _finish_plan(plan: Plan, candidates: list[Region], extras: list[Region],
                 cfg: Config, duration: float) -> Plan:
    """Pad, union and check the candidates, whatever produced them."""
    c = cfg.cut

    # What the interstitials removed that the speaker rules would not have
    # anyway. An ad inside a stretch of a speaker already being cut shortens
    # the episode by nothing extra, and counting it twice would make the feed
    # note claim more was removed than went.
    plan.interstitial_seconds = sum(
        r.duration for r in subtract(extras, candidates)
    )
    candidates = candidates + extras

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

    # The ceiling exists to catch a misidentified voice before it eats an
    # episode. In keep-only mode removing 90% is the job, not a symptom, so it
    # gets its own — high enough to allow a brief guest appearance, low enough
    # that "kept nothing at all" still refuses.
    ceiling = (c.keep_only_max_skip_fraction if plan.mode == "keep_only"
               else c.max_skip_fraction)
    setting = ("keep_only_max_skip_fraction" if plan.mode == "keep_only"
               else "max_skip_fraction")
    if plan.fraction > ceiling:
        detail = (
            "Check which speakers are flagged skip, and their match similarity."
            if plan.mode == "cut" else
            "That leaves almost nothing — check the voice was matched in this "
            "episode rather than merely being the closest guess."
        )
        raise CutRefused(
            f"the rules want to remove {plan.cut_seconds / 60:.1f} min of a "
            f"{duration / 60:.1f} min episode ({plan.fraction * 100:.1f}%), over the "
            f"{setting} ceiling of {ceiling * 100:.0f}%. "
            f"Refusing rather than emitting garbage. {detail}"
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
            "keep_only_min_cut_seconds": c.keep_only_min_cut_seconds,
            "keep_only_max_skip_fraction": c.keep_only_max_skip_fraction,
        },
        "mode": plan.mode,
        "skipped_labels": plan.skipped_labels,
        "kept_labels": plan.kept_labels,
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
            "interstitial_seconds": round(plan.interstitial_seconds, 3),
        },
        "cuts": [asdict(r) for r in plan.cuts],
        # Recorded rather than left to be derived: timeline.py maps original
        # timestamps onto the edit from this, and reconstructing the complement
        # means re-deriving the dropped-short-keep rule from the config.
        "keeps": [asdict(r) for r in plan.keeps],
        "decisions": [asdict(d) for d in plan.decisions],
    }
    # render() makes its own parent, but the log is written first — and for a
    # person feed this is the first thing ever written to that directory.
    dest.parent.mkdir(parents=True, exist_ok=True)
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
