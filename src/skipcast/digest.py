"""One file, the length of your commute, made of the best of what is waiting.

The problem this project started from is that there is more podcast than time.
Every phase so far has attacked it by making episodes shorter. This attacks it
from the other end: say how long you have, and get back a single thing to play.

## What goes in

Candidates are topics, not episodes — the spans indexed from each summary. A
topic is the smallest unit that still makes sense on its own: it opens with
someone introducing a subject and runs until the next one starts.

Ranking is deliberately simple and explainable, because a digest that picks
badly is worse than no digest and you have to be able to see why something was
chosen:

- unplayed episodes first, and unplayed *recent* ones above older ones
- one topic per episode before any episode contributes a second, so a digest
  spans the library rather than replaying one show
- topics too short to be worth a join, or long enough to eat the whole budget,
  are skipped

## Where the audio comes from

The **edited** file, not the source. A digest of a library that removes a
speaker and the ads should not quietly reintroduce them, so each topic's span
is mapped through that episode's cut log first. Pieces that were removed
entirely are dropped rather than silently shortened.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from . import audio, cut as cutter, db, timeline
from .config import Config

# A piece shorter than this is not a topic, it is a fragment; longer than this
# and one subject swallows the whole digest.
MIN_PIECE_SECONDS = 60.0
MAX_PIECE_SECONDS = 900.0


class DigestError(RuntimeError):
    pass


@dataclass
class Piece:
    episode_key: str
    episode_title: str
    feed_slug: str
    feed_title: str
    topic: str
    one_line: str
    # Positions in the *edited* audio, which is what gets cut from.
    start: float
    end: float
    seconds: float


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def digest_dir(cfg: Config) -> Path:
    return cfg.data_dir / "digests"


def candidates(conn, feed_slug: str | None = None,
               unplayed_only: bool = True) -> list[Piece]:
    """Every topic that could go in, best first, already mapped to edit time."""
    sql = """SELECT t.*, e.key AS episode_key, e.title AS episode_title,
                    e.cut_path, e.cuts_path, e.original_seconds, e.published_ts,
                    f.slug AS feed_slug, f.title AS feed_title,
                    COALESCE(p.finished, 0) AS finished,
                    COALESCE(p.position, 0) AS position
               FROM topics t
               JOIN episodes e ON e.id = t.episode_id
               JOIN feeds f ON f.id = e.feed_id
               LEFT JOIN playback p ON p.episode_key = e.key
              WHERE e.status = 'ready' AND e.cut_path IS NOT NULL
                AND t.start_seconds IS NOT NULL AND t.end_seconds IS NOT NULL"""
    params: list = []
    if feed_slug:
        sql += " AND f.slug = ?"
        params.append(feed_slug)
    if unplayed_only:
        sql += " AND COALESCE(p.finished, 0) = 0"
    sql += " ORDER BY e.published_ts DESC, t.position"

    timelines: dict[str, timeline.Timeline] = {}
    out: list[Piece] = []
    for r in conn.execute(sql, params):
        path = Path(r["cut_path"] or "")
        if not path.is_file():
            continue
        if r["episode_key"] not in timelines:
            timelines[r["episode_key"]] = timeline.load(
                r["cuts_path"], float(r["original_seconds"] or 0)
            )
        tl = timelines[r["episode_key"]]
        # Map the topic onto the edit. A topic whose opening was removed still
        # counts — it starts at the next thing the listener actually hears.
        start = tl.to_cut(float(r["start_seconds"]))
        end = tl.to_cut(float(r["end_seconds"]))
        span = end - start
        if span < MIN_PIECE_SECONDS or span > MAX_PIECE_SECONDS:
            continue
        out.append(Piece(
            episode_key=r["episode_key"],
            episode_title=r["episode_title"] or "",
            feed_slug=r["feed_slug"] or "",
            feed_title=r["feed_title"] or "",
            topic=r["title"],
            one_line=r["one_line"] or "",
            start=round(start, 3),
            end=round(end, 3),
            seconds=round(span, 3),
        ))
    return out


def select(pool: list[Piece], budget_seconds: float) -> list[Piece]:
    """Fill the budget, spreading across episodes before repeating one.

    Round-robin rather than greedy-by-rank: a straight ranking would hand back
    forty minutes of whichever episode happened to sort first, which is not a
    digest of anything.
    """
    by_episode: dict[str, list[Piece]] = {}
    for p in pool:
        by_episode.setdefault(p.episode_key, []).append(p)

    order = list(by_episode)
    chosen: list[Piece] = []
    total = 0.0
    round_no = 0
    while order:
        progressed = False
        for key in list(order):
            queue = by_episode[key]
            if round_no >= len(queue):
                order.remove(key)
                continue
            piece = queue[round_no]
            if total + piece.seconds > budget_seconds:
                # Do not stop outright: a later, shorter piece may still fit,
                # and stopping at the first over-budget candidate leaves
                # minutes of the allowance unused.
                continue
            chosen.append(piece)
            total += piece.seconds
            progressed = True
        round_no += 1
        if not progressed:
            break

    # Play them newest-episode-first, and in episode order within that, so the
    # result follows an argument rather than jumping mid-discussion.
    position = {k: i for i, k in enumerate(by_episode)}
    chosen.sort(key=lambda p: (position[p.episode_key], p.start))
    return chosen


def build(conn, cfg: Config, minutes: float, feed_slug: str | None = None,
          unplayed_only: bool = True, title: str | None = None) -> dict:
    """Assemble a digest and record what went into it."""
    if minutes <= 0:
        raise DigestError("a digest needs a positive number of minutes")

    pool = candidates(conn, feed_slug, unplayed_only)
    if not pool:
        raise DigestError(
            "nothing to build from. Digests are made of summarised topics — "
            "summarise some episodes, then run: skipcast index"
        )
    chosen = select(pool, minutes * 60)
    if not chosen:
        shortest = min(p.seconds for p in pool) / 60
        raise DigestError(
            f"no topic fits in {minutes:g} minutes; the shortest available is "
            f"{shortest:.1f} min"
        )

    stamp = dt.datetime.now(dt.timezone.utc)
    key = "d" + hashlib.sha1(
        (stamp.isoformat() + str(minutes) + str(len(chosen))).encode()
    ).hexdigest()[:11]
    dest = digest_dir(cfg) / f"{key}.mp3"

    _log(f"[digest] {len(chosen)} topic(s) from "
         f"{len({p.episode_key for p in chosen})} episode(s), "
         f"{sum(p.seconds for p in chosen) / 60:.1f} min planned")
    for p in chosen:
        _log(f"[digest]   {p.seconds / 60:4.1f} min  {p.topic[:52]} ({p.feed_slug})")

    paths: dict[str, Path] = {}
    spans = []
    for p in chosen:
        if p.episode_key not in paths:
            row = db.get_episode_by_key(conn, p.episode_key)
            paths[p.episode_key] = Path(row["cut_path"] if row else "")
        spans.append((paths[p.episode_key], p.start, p.end))
    cutter.stitch(spans, cfg, dest)
    seconds = audio.duration_seconds(dest)
    title = title or (
        f"{stamp.strftime('%-d %b')} digest — {len(chosen)} topics, "
        f"{seconds / 60:.0f} min"
    )
    conn.execute(
        """INSERT INTO digests (key, title, minutes, seconds, audio_path,
                                pieces, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (key, title, minutes, seconds, str(dest),
         json.dumps([asdict(p) for p in chosen]),
         stamp.isoformat(timespec="seconds")),
    )
    conn.commit()
    return {"key": key, "title": title, "seconds": seconds,
            "pieces": [asdict(p) for p in chosen]}


def recent(conn, limit: int = 20) -> list:
    return conn.execute(
        "SELECT * FROM digests ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def get(conn, key: str):
    return conn.execute("SELECT * FROM digests WHERE key = ?", (key,)).fetchone()


def remove(conn, key: str) -> bool:
    cur = conn.execute("DELETE FROM digests WHERE key = ?", (key,))
    conn.commit()
    return cur.rowcount > 0
