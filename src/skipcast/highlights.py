"""Saved moments — the thirty seconds you just heard and want to keep.

Capture is retroactive. You never know a passage was worth saving until after
it has been said, so the button reaches backwards from wherever playback is
rather than starting a recording.

## What a highlight actually is

A range, not a file. The source audio is kept and never rewritten, so the clip
can be rebuilt at any time, and most highlights are never shared and so never
need rendering at all. That also settles the question of whether a highlight
is "the text" or "the audio": it is neither, it is a position, and both fall
out of it on demand.

## Which clock

Original-audio seconds, always. The player reports a position in the *edited*
file, so capture converts immediately and stores the result. Storing what the
player said would have been simpler by one function call and wrong by minutes
the first time an episode was recut — which happens routinely here, since ads
are found in the transcript and removed in a second pass after the episode is
already playable.

## Why a list of ranges

Because the window can straddle something that was cut. Thirty seconds of the
edit either side of a removed ad read is two disjoint stretches of the
original, and a highlight that stored a single start and end would span the
gap and hand back the ad — audio the listener never heard and specifically
asked not to. `timeline.original_spans` does the conversion; this module just
refuses to flatten it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from . import db, timeline, transcribe as stt
from .config import Config

# Shorter than this is a mis-tap, not a moment.
MIN_SECONDS = 1.0
# Gaps smaller than this between kept pieces are not worth preserving as a
# join; they are rounding around a crossfade.
GLUE_SECONDS = 0.25


class HighlightError(RuntimeError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def clips_dir(cfg: Config) -> Path:
    return cfg.data_dir / "clips"


def _spans_to_json(spans) -> str:
    return json.dumps([{"start": round(s.start, 3), "end": round(s.end, 3)}
                       for s in spans])


def pieces_of(row) -> list[timeline.Span]:
    """The stored ranges of a highlight row, in original-audio seconds."""
    try:
        raw = json.loads(row["pieces"] or "[]")
    except ValueError:
        return []
    return [timeline.Span(float(p["start"]), float(p["end"])) for p in raw]


def total_seconds(spans) -> float:
    return sum(s.duration for s in spans)


def _union(spans: list[timeline.Span]) -> list[timeline.Span]:
    """Sort and merge overlapping or barely-separated ranges."""
    out: list[timeline.Span] = []
    for s in sorted(spans, key=lambda x: x.start):
        if out and s.start - out[-1].end <= GLUE_SECONDS:
            out[-1] = timeline.Span(out[-1].start, max(out[-1].end, s.end))
        else:
            out.append(timeline.Span(s.start, s.end))
    return out


def _cap(spans: list[timeline.Span], limit: float) -> list[timeline.Span]:
    """Trim to a maximum duration, keeping the end.

    The end is the anchor because capture is retroactive: the listener pressed
    the button just after the thing they wanted, so the last second is the one
    certain to matter and the first is the one most likely to be padding.
    """
    if limit <= 0 or total_seconds(spans) <= limit:
        return spans
    kept: list[timeline.Span] = []
    budget = limit
    for s in reversed(spans):
        if budget <= 0:
            break
        if s.duration <= budget:
            kept.append(s)
            budget -= s.duration
        else:
            kept.append(timeline.Span(s.end - budget, s.end))
            budget = 0
    return list(reversed(kept))


def quote_for(transcript: dict, spans: list[timeline.Span]) -> tuple[str, str | None]:
    """The words spoken under a set of ranges, and who did most of the talking.

    Only the overlapping part of each turn is taken, so a highlight that starts
    mid-sentence quotes from mid-sentence rather than dragging in the whole
    preceding monologue.
    """
    said: list[tuple[str, str]] = []
    talk: dict[str, float] = {}
    for turn in transcript.get("turns") or []:
        t_start, t_end = float(turn["start"]), float(turn["end"])
        who = turn.get("speaker_name") or turn.get("speaker_label") or "?"
        for span in spans:
            a, b = max(t_start, span.start), min(t_end, span.end)
            if b - a <= 0.05:
                continue
            lo, hi = stt.char_range(turn, a, b)
            text = (turn.get("text") or "")[lo:hi].strip()
            if not text:
                continue
            talk[who] = talk.get(who, 0.0) + (b - a)
            if said and said[-1][0] == who:
                said[-1] = (who, f"{said[-1][1]} {text}")
            else:
                said.append((who, text))

    if not said:
        return "", None
    dominant = max(talk, key=talk.get) if talk else None
    if len({who for who, _ in said}) == 1:
        return said[0][1], dominant
    # More than one voice: keep the attribution, it is half the point of having
    # diarized the episode at all.
    return "\n".join(f"{who}: {text}" for who, text in said), dominant


def _transcript_for(row) -> dict | None:
    path = Path(row["transcript_path"] or "")
    if not path.is_file():
        return None
    try:
        return stt.load(path)
    except (ValueError, OSError):
        return None


def _describe(row, spans: list[timeline.Span]) -> tuple[str, str | None]:
    transcript = _transcript_for(row)
    if transcript is None:
        return "", None
    return quote_for(transcript, spans)


def _overlapping(conn: sqlite3.Connection, episode_id: int,
                 start: float, end: float, window: float):
    """The most recent highlight of this episode near the given range.

    Two taps a few seconds apart are one moment noticed twice, not two
    highlights; extending beats accumulating near-duplicates that then have to
    be tidied up by hand.
    """
    for row in conn.execute(
        "SELECT * FROM highlights WHERE episode_id = ? ORDER BY id DESC",
        (episode_id,),
    ):
        spans = pieces_of(row)
        if not spans:
            continue
        lo = min(s.start for s in spans)
        hi = max(s.end for s in spans)
        if start <= hi + window and end >= lo - window:
            return row
    return None


def capture(conn: sqlite3.Connection, cfg: Config, episode_key: str,
            position: float, lookback: float | None = None,
            note: str | None = None) -> dict:
    """Save what was just playing.

    `position` is where the player is, in the *edited* file — that is the only
    clock the browser knows about. Everything stored is converted out of it.
    """
    row = db.get_episode_by_key(conn, episode_key)
    if row is None:
        raise HighlightError(f"no episode {episode_key}")

    lookback = cfg.highlight.lookback_seconds if lookback is None else lookback
    cut_end = max(0.0, float(position))
    cut_start = max(0.0, cut_end - max(0.0, float(lookback)))
    if cut_end - cut_start < MIN_SECONDS:
        raise HighlightError(
            "nothing to save yet — there is less than a second of playback "
            "behind this point"
        )

    tl = timeline.for_episode(row)
    spans = _union(tl.original_spans(cut_start, cut_end))
    if not spans:
        raise HighlightError("that stretch of the episode has no audio behind it")

    existing = _overlapping(conn, row["id"], min(s.start for s in spans),
                            max(s.end for s in spans),
                            cfg.highlight.merge_window_seconds)
    if existing is not None:
        spans = _union(pieces_of(existing) + spans)

    spans = _cap(spans, cfg.highlight.max_seconds)
    quote, speaker = _describe(row, spans)

    if existing is not None:
        conn.execute(
            """UPDATE highlights
                  SET pieces = ?, seconds = ?, quote = ?, speaker_name = ?,
                      note = COALESCE(?, note), audio_path = NULL, updated_at = ?
                WHERE id = ?""",
            (_spans_to_json(spans), round(total_seconds(spans), 3), quote,
             speaker, note, _now(), existing["id"]),
        )
        conn.commit()
        # The cached audio described the old range, so it is dropped above and
        # the next play re-renders. Deleting the file is left to the render
        # path, which owns the directory.
        return as_dict(get(conn, existing["key"]))

    stamp = _now()
    key = "h" + hashlib.sha1(
        f"{episode_key}:{spans[0].start}:{spans[-1].end}:{stamp}".encode()
    ).hexdigest()[:11]
    conn.execute(
        """INSERT INTO highlights (key, episode_id, pieces, seconds, quote,
                                   speaker_name, note, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (key, row["id"], _spans_to_json(spans), round(total_seconds(spans), 3),
         quote, speaker, note, stamp),
    )
    conn.commit()
    return as_dict(get(conn, key))


def retrim(conn: sqlite3.Connection, cfg: Config, key: str,
           start: float, end: float) -> dict:
    """Narrow a highlight to a sub-range of itself, in original seconds.

    Only ever narrows. Widening would need audio outside what was captured,
    which is a different operation with a different failure mode (the episode
    may be gone), and conflating the two makes an accidental drag able to pull
    in material the listener never chose.
    """
    row = get(conn, key)
    if row is None:
        raise HighlightError(f"no highlight {key}")
    spans = pieces_of(row)
    if not spans:
        raise HighlightError("this highlight has no ranges to trim")

    lo, hi = (start, end) if end >= start else (end, start)
    kept = [timeline.Span(max(s.start, lo), min(s.end, hi)) for s in spans]
    kept = _union([s for s in kept if s.duration > 0.05])
    if not kept or total_seconds(kept) < MIN_SECONDS:
        raise HighlightError("that leaves less than a second of audio")

    episode = conn.execute("SELECT * FROM episodes WHERE id = ?",
                           (row["episode_id"],)).fetchone()
    quote, speaker = _describe(episode, kept) if episode else ("", None)
    conn.execute(
        """UPDATE highlights
              SET pieces = ?, seconds = ?, quote = ?, speaker_name = ?,
                  audio_path = NULL, updated_at = ?
            WHERE id = ?""",
        (_spans_to_json(kept), round(total_seconds(kept), 3), quote, speaker,
         _now(), row["id"]),
    )
    conn.commit()
    return as_dict(get(conn, key))


def backfill(conn: sqlite3.Connection, episode_id: int) -> int:
    """Fill in quotes for highlights saved before the transcript existed.

    Transcription is deferred to its own worker, so an episode is playable —
    and therefore highlightable — for as long as it takes Whisper to catch up.
    A highlight saved in that window is a range with no words attached until
    this runs.
    """
    episode = conn.execute("SELECT * FROM episodes WHERE id = ?",
                           (episode_id,)).fetchone()
    if episode is None:
        return 0
    transcript = _transcript_for(episode)
    if transcript is None:
        return 0

    filled = 0
    for row in conn.execute(
        "SELECT * FROM highlights WHERE episode_id = ? AND "
        "(quote IS NULL OR quote = '')", (episode_id,)
    ).fetchall():
        quote, speaker = quote_for(transcript, pieces_of(row))
        if not quote:
            continue
        conn.execute(
            "UPDATE highlights SET quote = ?, speaker_name = ?, updated_at = ? "
            "WHERE id = ?",
            (quote, speaker, _now(), row["id"]),
        )
        filled += 1
    if filled:
        conn.commit()
    return filled


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def _sentence_offsets(text: str) -> list[tuple[int, int]]:
    """Character ranges of each sentence in a stretch of text."""
    out: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_BREAK.finditer(text):
        if match.start() > cursor:
            out.append((cursor, match.start()))
        cursor = match.end()
    if cursor < len(text):
        out.append((cursor, len(text)))
    return out


def segments(conn: sqlite3.Connection, key: str) -> list[dict]:
    """The clip's words, split into sentences that each know when they were said.

    Sentences are the unit worth offering as a trim target. They are bounded by
    the pauses the speaker actually took, so a clip cut on them opens and closes
    cleanly, where a word-precise drag lands mid-breath and sounds broken. The
    per-word timings make the boundaries exact; without them the split still
    works, just a word or so loose.
    """
    row = get(conn, key)
    if row is None:
        raise HighlightError(f"no highlight {key}")
    episode = conn.execute("SELECT * FROM episodes WHERE id = ?",
                           (row["episode_id"],)).fetchone()
    transcript = _transcript_for(episode) if episode else None
    if transcript is None:
        return []

    spans = pieces_of(row)
    out: list[dict] = []
    for turn in transcript.get("turns") or []:
        t_start, t_end = float(turn["start"]), float(turn["end"])
        who = turn.get("speaker_name") or turn.get("speaker_label") or "?"
        whole = turn.get("text") or ""
        for span in spans:
            a, b = max(t_start, span.start), min(t_end, span.end)
            if b - a <= 0.05:
                continue
            lo, hi = stt.char_range(turn, a, b)
            for s_lo, s_hi in _sentence_offsets(whole[lo:hi]):
                text = whole[lo + s_lo:lo + s_hi].strip()
                if not text:
                    continue
                start, end = stt.time_range(turn, lo + s_lo, lo + s_hi)
                # Never offer a boundary outside the clip itself: trimming
                # narrows, so a sentence that runs past the edge stops there.
                start, end = max(start, span.start), min(end, span.end)
                if end - start <= 0.05:
                    continue
                out.append({"start": round(start, 3), "end": round(end, 3),
                            "speaker": who, "text": text})
    out.sort(key=lambda s: s["start"])
    return out


def render(conn: sqlite3.Connection, cfg: Config, key: str) -> Path:
    """Produce the clip's audio, reusing it if it already exists.

    Cut from the *source*, not the edit. The stored ranges came out of the
    edit in the first place — every one of them is audio the listener actually
    heard — and the source is the one file here that is never rewritten, so a
    clip rendered from it is reproducible for as long as the download is kept.
    Rendering off the edit instead would put every clip at the mercy of the
    next recut.

    Cheap enough to do on demand: this is seconds of audio, where the same
    function called on a whole episode takes minutes.
    """
    from . import cut as cutter

    row = get(conn, key)
    if row is None:
        raise HighlightError(f"no highlight {key}")

    cached = Path(row["audio_path"] or "")
    if cached.is_file():
        return cached

    episode = conn.execute("SELECT * FROM episodes WHERE id = ?",
                           (row["episode_id"],)).fetchone()
    source = Path((episode["source_path"] if episode else "") or "")
    if not source.is_file():
        raise HighlightError(
            "the episode's source audio is no longer on disk, so this clip "
            "cannot be rebuilt"
        )

    spans = pieces_of(row)
    if not spans:
        raise HighlightError("this highlight has no ranges to render")

    dest = clips_dir(cfg) / f"{key}.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    cutter.stitch([(source, s.start, s.end) for s in spans], cfg, dest)

    conn.execute("UPDATE highlights SET audio_path = ? WHERE id = ?",
                 (str(dest), row["id"]))
    conn.commit()
    return dest


def get(conn: sqlite3.Connection, key: str):
    return conn.execute("SELECT * FROM highlights WHERE key = ?", (key,)).fetchone()


def recent(conn: sqlite3.Connection, limit: int = 100,
           episode_key: str | None = None) -> list[sqlite3.Row]:
    """Newest first, with enough of the episode and feed to show a card."""
    sql = """SELECT h.*, e.key AS episode_key, e.title AS episode_title,
                    e.published_ts, e.cuts_path, e.original_seconds,
                    f.slug AS feed_slug, f.title AS feed_title
               FROM highlights h
               JOIN episodes e ON e.id = h.episode_id
               JOIN feeds f ON f.id = e.feed_id"""
    params: list = []
    if episode_key:
        sql += " WHERE e.key = ?"
        params.append(episode_key)
    sql += " ORDER BY h.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def remove(conn: sqlite3.Connection, key: str) -> bool:
    row = get(conn, key)
    if row is None:
        return False
    path = Path(row["audio_path"] or "")
    conn.execute("DELETE FROM highlights WHERE id = ?", (row["id"],))
    conn.commit()
    if path.is_file():
        try:
            path.unlink()
        except OSError:  # a clip we cannot delete is not a failed delete
            pass
    return True


def as_dict(row) -> dict:
    """A highlight shaped for the API, with its ranges already unpacked."""
    out = {k: row[k] for k in row.keys() if k not in ("pieces", "cuts_path")}
    out["pieces"] = [{"start": s.start, "end": s.end} for s in pieces_of(row)]
    return out
