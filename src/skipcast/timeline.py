"""Mapping a timestamp in the original episode onto the edited one.

Everything downstream of diarization is timed against the *source* audio: the
segments file, the transcript, and every timestamp the summariser quotes. What
the phone plays is the *cut* file. Those two clocks diverge the moment anything
is removed, and they diverge by more with every cut — so a search hit at 41:20
of the original lands minutes away from 41:20 of the edit.

This module is the one place that converts between them. It reads the cut log,
which already records every removed region, and reconstructs the kept timeline
from it.

Crossfades are accounted for: each join overlaps `crossfade` seconds of the two
pieces it connects, so the output runs shorter than the sum of the kept pieces
by crossfade x joins. Inside a fade the mapping is off by at most half a
crossfade — 75 ms at the default — which is far below the accuracy of the
timestamps being mapped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Span:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Timeline:
    """The kept pieces of an episode, in original-audio coordinates."""

    keeps: list[Span]
    crossfade: float = 0.0
    original_seconds: float = 0.0

    @property
    def identity(self) -> bool:
        """True when nothing was removed, so both clocks agree."""
        return len(self.keeps) <= 1 and (
            not self.keeps or self.keeps[0].start <= 0.001
        )

    def _offsets(self) -> list[float]:
        """Where each kept piece begins in the edited file."""
        out: list[float] = []
        cursor = 0.0
        for i, k in enumerate(self.keeps):
            if i:
                cursor -= self.crossfade
            out.append(max(0.0, cursor))
            cursor += k.duration
        return out

    @property
    def result_seconds(self) -> float:
        total = sum(k.duration for k in self.keeps)
        return max(0.0, total - self.crossfade * max(0, len(self.keeps) - 1))

    def was_cut(self, t: float) -> bool:
        return not any(k.start <= t <= k.end for k in self.keeps)

    def to_cut(self, t: float) -> float:
        """Position in the edited file for a timestamp in the original.

        A timestamp that fell inside a removed region maps to the start of the
        next kept piece — the moment the listener actually hears next. Ask
        was_cut() if you need to tell the two cases apart.
        """
        if not self.keeps:
            return 0.0
        offsets = self._offsets()
        for i, k in enumerate(self.keeps):
            if t < k.start:
                return offsets[i]          # inside a cut: next thing they hear
            if t <= k.end:
                return offsets[i] + (t - k.start)
        return self.result_seconds         # past the end of the last keep

    def to_original(self, t: float) -> float:
        """The inverse: where a position in the edit sits in the original."""
        if not self.keeps:
            return t
        offsets = self._offsets()
        for i, k in enumerate(self.keeps):
            if t <= offsets[i] + k.duration:
                return k.start + max(0.0, t - offsets[i])
        return self.keeps[-1].end


def identity_timeline(duration: float = 0.0) -> Timeline:
    """For an episode with no cut log — the two clocks are the same."""
    return Timeline(keeps=[Span(0.0, duration)] if duration else [],
                    crossfade=0.0, original_seconds=duration)


def from_cut_log(log: dict) -> Timeline:
    """Rebuild the kept timeline from a .cuts.json.

    Logs written before keeps were recorded only carry the removed regions, so
    the complement is derived here. That reproduces what build_plan() did,
    including its rule that a kept piece shorter than the crossfade is dropped
    (it would be swallowed by the fade).
    """
    summary = log.get("summary") or {}
    duration = float(summary.get("original_seconds") or 0.0)
    crossfade = float((log.get("config") or {}).get("crossfade_seconds") or 0.0)

    recorded = log.get("keeps")
    if recorded:
        keeps = [Span(float(k["start"]), float(k["end"])) for k in recorded]
        return Timeline(keeps, crossfade, duration)

    cuts = sorted(
        (Span(float(c["start"]), float(c["end"])) for c in log.get("cuts") or []),
        key=lambda s: s.start,
    )
    keeps: list[Span] = []
    cursor = 0.0
    for c in cuts:
        if c.start > cursor:
            keeps.append(Span(cursor, c.start))
        cursor = max(cursor, c.end)
    if cursor < duration:
        keeps.append(Span(cursor, duration))
    keeps = [k for k in keeps if k.duration >= crossfade]
    return Timeline(keeps, crossfade, duration)


def load(path: str | Path | None, duration: float = 0.0) -> Timeline:
    """Timeline for an episode, falling back to identity when there is no log."""
    if not path:
        return identity_timeline(duration)
    p = Path(path)
    if not p.is_file():
        return identity_timeline(duration)
    try:
        return from_cut_log(json.loads(p.read_text()))
    except (ValueError, KeyError):
        # A malformed log must not take out search or the summary links; an
        # unmapped timestamp is wrong by minutes, a missing feature by all of it.
        return identity_timeline(duration)


def for_episode(row) -> Timeline:
    """Timeline for an episodes-table row."""
    return load(row["cuts_path"] if "cuts_path" in row.keys() else None,
                float(row["original_seconds"] or 0.0))
