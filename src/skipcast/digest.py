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

## Stories, not shuffle

Topics that are the same story told by different shows (judged by the overlap
module's scorer — shared entities and title terms) play back to back rather
than being scattered by the rotation. The rotation still decides which stories
get in; the story grouping decides what sits next to what, which is the
difference between a digest and a shuffle.

Every piece records *why* it was picked, in words, in the pieces JSON. The
selection rules only stay simple if you can check them from the outside.

## One digest, one theme

Shows that keep telling the same stories are one theme; shows that never do
are not, and forcing them into one file makes a grab-bag, not a digest. The
theme graph needs no configuration: two feeds are joined whenever any two of
their topics — across the whole library, not just what is unplayed — were
judged one story, and the connected components are the themes. A finance show
and a history show never share a story, so they never share a digest.

A build picks one theme: the one with the newest unplayed material that can
fill at least half the asked-for time, falling back to the fullest theme when
none can. What the other themes had is set aside for the next build, and the
digest records which shows it drew from. Asking for a specific feed skips all
of this — a scope the user chose is not to be second-guessed.

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

from . import audio, cut as cutter, db, overlap, timeline
from .config import Config

# A piece shorter than this is not a topic, it is a fragment; longer than this
# and one subject swallows the whole digest.
MIN_PIECE_SECONDS = 60.0
MAX_PIECE_SECONDS = 900.0
# A topic too long to be a piece still gets in — as its opening stretch. The
# opening is where the subject is framed, which is the part a digest wants.
LONG_TOPIC_EXCERPT_SECONDS = 480.0


class DigestError(RuntimeError):
    pass


# Two takes on a story published further apart than this are a subject, not
# a story — same window the overlap views use.
STORY_WINDOW_DAYS = 21


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
    # Selection provenance. story numbers group takes of the same story;
    # why says, in words, how this piece earned its place.
    topic_id: int = 0
    published_ts: int = 0
    story: int = 0
    why: str = ""
    # When the piece is the opening of a longer topic, how long the whole
    # topic ran — zero means the piece is the whole topic.
    excerpt_of_seconds: float = 0.0


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def digest_dir(cfg: Config) -> Path:
    return cfg.data_dir / "digests"


def _passage_ends(conn, episode_key: str) -> list[float]:
    """Where spoken passages end in this episode, original clock, sorted.

    From the transcript search index, which may not exist (FTS5 missing, or
    the episode not yet transcribed) — an empty list just means excerpts end
    at the time cap instead of at a pause.
    """
    try:
        return sorted(float(r["end"]) for r in conn.execute(
            "SELECT end FROM transcript_fts WHERE episode_key = ?",
            (episode_key,)))
    except Exception:  # noqa: BLE001 — no index is not an error here
        return []


def candidates(conn, feed_slug: str | None = None,
               unplayed_only: bool = True) -> list[Piece]:
    """Every topic that could go in, best first, already mapped to edit time."""
    sql = """SELECT t.*, e.key AS episode_key, e.title AS episode_title,
                    e.feed_id, e.cut_path, e.cuts_path, e.original_seconds,
                    e.published_ts,
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

    from . import entities

    timelines: dict[str, timeline.Timeline] = {}
    passage_ends: dict[str, list[float]] = {}
    # Chapters removed from the library for their subject must not come back
    # in through a digest. Same rule the edited audio follows: a digest of a
    # library that skips something does not quietly reintroduce it.
    skipped: dict[int, set[int]] = {}
    out: list[Piece] = []
    for r in conn.execute(sql, params):
        path = Path(r["cut_path"] or "")
        if not path.is_file():
            continue
        if r["episode_id"] not in skipped:
            skipped[r["episode_id"]] = {
                ch["topic_id"] for ch in entities.skipped_chapters(
                    conn, r["episode_id"], r["feed_id"])
            }
        if r["id"] in skipped[r["episode_id"]]:
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
        if span < MIN_PIECE_SECONDS:
            continue
        excerpt_of = 0.0
        if span > MAX_PIECE_SECONDS:
            # Too long to include whole; include the opening stretch instead,
            # ended where a spoken passage ends rather than mid-sentence when
            # the transcript index can say where that is.
            if r["episode_key"] not in passage_ends:
                passage_ends[r["episode_key"]] = _passage_ends(conn, r["episode_key"])
            cap = start + LONG_TOPIC_EXCERPT_SECONDS
            cut_end = cap
            for b in passage_ends[r["episode_key"]]:
                bc = tl.to_cut(b)
                if start + MIN_PIECE_SECONDS <= bc <= cap:
                    cut_end = bc
                elif bc > cap:
                    break
            excerpt_of = round(span, 3)
            end = cut_end
            span = end - start
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
            topic_id=int(r["id"]),
            published_ts=int(r["published_ts"] or 0),
            excerpt_of_seconds=excerpt_of,
        ))
    return out


# Two topics are the same story when they share this many whole entities, or
# one whole entity plus this many words drawn from titles and entity values.
MIN_STORY_TOKENS = 3


def _same_story(a_ents: set, b_ents: set,
                a_toks: set, b_toks: set) -> list[str] | None:
    """What two topics share, if it is enough to call them one story.

    Looser than the overlap module's pairwise score, deliberately. That gate
    backs a claim shown in the UI — "also covered by" — where a false positive
    lies to the user. Here the stake is only which pieces sit next to each
    other, so one shared named entity plus a few shared significant words is
    enough: "Chip Stocks Crash & Margin Call" and "South Korean Market Crash"
    both naming Aschenbrenner is one story, even though the entity strings
    around him were normalised differently by two different summary runs.
    """
    shared_ents = a_ents & b_ents
    if len(shared_ents) >= overlap.MIN_SHARED_ENTITIES:
        return sorted(shared_ents)[:6]
    shared_toks = a_toks & b_toks
    if shared_ents and len(shared_toks) >= MIN_STORY_TOKENS:
        ent_words = {t for e in shared_ents for t in overlap.terms(e)}
        return sorted(shared_ents) + sorted(shared_toks - ent_words)[:4]
    return None


def stories(conn, pool: list[Piece]) -> tuple[dict[int, int], dict[int, list[str]]]:
    """Group the pool into stories.

    Returns (story id by topic id, shared values by topic id). The pool is
    newest first, so each story's seed is its newest take; later pieces that
    match a seed join that story. Same-episode topics never merge — within
    one episode the summariser already decided they were different.
    """
    ents = overlap._topic_entities(conn)

    def tokens(p: Piece) -> set[str]:
        toks = set(overlap.terms(p.topic))
        for v in ents.get(p.topic_id, set()):
            toks |= overlap.terms(v)
        return toks

    window = STORY_WINDOW_DAYS * 86400
    story_of: dict[int, int] = {}
    shared_of: dict[int, list[str]] = {}
    seeds: list[tuple[Piece, set, set]] = []
    for p in pool:
        p_ents = ents.get(p.topic_id, set())
        p_toks = tokens(p)
        for si, (seed, s_ents, s_toks) in enumerate(seeds):
            if seed.episode_key == p.episode_key:
                continue
            if abs(seed.published_ts - p.published_ts) > window:
                continue
            shared = _same_story(s_ents, p_ents, s_toks, p_toks)
            if shared is not None:
                story_of[p.topic_id] = si
                shared_of[p.topic_id] = shared
                break
        else:
            story_of[p.topic_id] = len(seeds)
            seeds.append((p, p_ents, p_toks))
    return story_of, shared_of


def feed_themes(conn, library: list[Piece]) -> dict[str, str]:
    """Each feed's theme, from which shows tell the same stories.

    Union-find over feeds: any story with takes in two feeds joins those
    feeds; the connected components are the themes. Computed over the whole
    library rather than the unplayed pool so the graph reflects how the shows
    relate, not what happens to be unheard this week.
    """
    story_of, _ = stories(conn, library)
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_story: dict[int, list[Piece]] = {}
    for p in library:
        find(p.feed_slug)
        by_story.setdefault(story_of[p.topic_id], []).append(p)
    for members in by_story.values():
        for m in members[1:]:
            parent[find(m.feed_slug)] = find(members[0].feed_slug)
    return {slug: find(slug) for slug in parent}


def pick_theme(pool: list[Piece], root_of: dict[str, str],
               budget_seconds: float) -> tuple[list[Piece], list[str]]:
    """Narrow the pool to one theme; also say which feeds were set aside.

    Themes are tried newest-unplayed-first; the first that can fill at least
    half the budget wins, and if none can, the fullest one does — a thin
    coherent digest beats a padded incoherent one.
    """
    groups: dict[str, list[Piece]] = {}
    for p in pool:
        groups.setdefault(root_of.get(p.feed_slug, p.feed_slug), []).append(p)
    if len(groups) <= 1:
        return pool, []
    ranked = sorted(groups.values(),
                    key=lambda g: max(p.published_ts for p in g), reverse=True)
    chosen = next((g for g in ranked
                   if sum(p.seconds for p in g) >= budget_seconds / 2), None)
    if chosen is None:
        chosen = max(ranked, key=lambda g: sum(p.seconds for p in g))
    aside = sorted({p.feed_title or p.feed_slug
                    for g in groups.values() if g is not chosen for p in g})
    return chosen, aside


def select(pool: list[Piece], budget_seconds: float,
           story_of: dict[int, int] | None = None) -> list[list[Piece]]:
    """Fill the budget, spreading across episodes before repeating one.

    Round-robin rather than greedy-by-rank: a straight ranking would hand back
    forty minutes of whichever episode happened to sort first, which is not a
    digest of anything.

    Returns story blocks rather than a flat list. When a picked topic belongs
    to a story with takes in other episodes, those takes come in with it, right
    then, budget permitting — the same story from two shows plays back to back
    instead of turning up minutes apart.
    """
    story_of = story_of or {}
    takes: dict[int, list[Piece]] = {}
    for p in pool:
        sid = story_of.get(p.topic_id)
        if sid is not None:
            takes.setdefault(sid, []).append(p)

    by_episode: dict[str, list[Piece]] = {}
    for p in pool:
        by_episode.setdefault(p.episode_key, []).append(p)

    order = list(by_episode)
    blocks: list[list[Piece]] = []
    taken: set[int] = set()
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
            if piece.topic_id in taken:
                # Already in as another story's take. Not a reason to stop.
                progressed = True
                continue
            if total + piece.seconds > budget_seconds:
                # Do not stop outright: a later, shorter piece may still fit,
                # and stopping at the first over-budget candidate leaves
                # minutes of the allowance unused.
                continue
            block = [piece]
            taken.add(piece.topic_id)
            total += piece.seconds
            sid = story_of.get(piece.topic_id)
            if sid is not None:
                for other in takes.get(sid, []):
                    if other.topic_id in taken or other.episode_key == piece.episode_key:
                        continue
                    if total + other.seconds > budget_seconds:
                        continue
                    block.append(other)
                    taken.add(other.topic_id)
                    total += other.seconds
            blocks.append(block)
            progressed = True
        round_no += 1
        if not progressed:
            break

    # Stories play newest-episode-first, in episode order within that, so the
    # result follows an argument rather than jumping mid-discussion. Within a
    # story: the seed's take first, then the other takes, newest first.
    position = {k: i for i, k in enumerate(by_episode)}
    blocks.sort(key=lambda b: (position[b[0].episode_key], b[0].start))
    for b in blocks:
        b[1:] = sorted(b[1:], key=lambda p: -p.published_ts)
    return blocks


def _watch_note(conn, piece: Piece, topic_ents: dict[int, set[str]]) -> str:
    """A watched term this topic mentions, if any — annotation, not ranking.

    Saying "mentions NVDA (watched)" is a fact about the piece; selection does
    not (yet) favour it. One term is enough for a why line.
    """
    ents = topic_ents.get(piece.topic_id, set())
    title = piece.topic.casefold()
    for r in conn.execute("SELECT term, term_norm FROM watchlist"):
        norm = r["term_norm"]
        if norm in title or any(norm in v for v in ents):
            return f" · mentions {r['term']} (watched)"
    return ""


def _explain(conn, pool: list[Piece], blocks: list[list[Piece]],
             shared_of: dict[int, list[str]]) -> None:
    """Fill in each chosen piece's story number and why line, in place."""
    newest_of_feed: dict[str, str] = {}
    for p in pool:  # pool is newest first
        newest_of_feed.setdefault(p.feed_slug, p.episode_key)
    topic_ents = overlap._topic_entities(conn)
    for story_no, block in enumerate(blocks, start=1):
        seed = block[0]
        seed.story = story_no
        seed.why = (
            f"from {seed.feed_title}'s newest unplayed episode"
            if newest_of_feed.get(seed.feed_slug) == seed.episode_key
            else f"unplayed, {seed.feed_title}"
        ) + _watch_note(conn, seed, topic_ents)
        for p in block[1:]:
            p.story = story_no
            shared = shared_of.get(p.topic_id, [])
            p.why = (f"another take on this story, from {p.feed_title}"
                     + (f" — shares {', '.join(shared[:3])}" if shared else "")
                     + _watch_note(conn, p, topic_ents))
        for p in block:
            if p.excerpt_of_seconds:
                p.why += (f" · the first {p.seconds / 60:.0f}m of a "
                          f"{p.excerpt_of_seconds / 60:.0f}m discussion")


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
    theme = None
    if feed_slug is None:
        library = candidates(conn, None, unplayed_only=False)
        pool, aside = pick_theme(pool, feed_themes(conn, library), minutes * 60)
        theme = " + ".join(sorted({p.feed_title or p.feed_slug for p in pool}))
        _log(f"[digest] theme: {theme}"
             + (f" (set aside, different theme: {', '.join(aside)})" if aside else ""))
    story_of, shared_of = stories(conn, pool)
    blocks = select(pool, minutes * 60, story_of)
    if not blocks:
        shortest = min(p.seconds for p in pool) / 60
        raise DigestError(
            f"no topic fits in {minutes:g} minutes; the shortest available is "
            f"{shortest:.1f} min"
        )
    _explain(conn, pool, blocks, shared_of)
    chosen = [p for block in blocks for p in block]

    stamp = dt.datetime.now(dt.timezone.utc)
    key = "d" + hashlib.sha1(
        (stamp.isoformat() + str(minutes) + str(len(chosen))).encode()
    ).hexdigest()[:11]
    dest = digest_dir(cfg) / f"{key}.mp3"

    _log(f"[digest] {len(blocks)} stor{'y' if len(blocks) == 1 else 'ies'}, "
         f"{len(chosen)} topic(s) from "
         f"{len({p.episode_key for p in chosen})} episode(s), "
         f"{sum(p.seconds for p in chosen) / 60:.1f} min planned")
    for p in chosen:
        _log(f"[digest]   {p.seconds / 60:4.1f} min  {p.topic[:48]} — {p.why}")

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
        f"{stamp.strftime('%-d %b')} digest — "
        f"{len(blocks)} stor{'y' if len(blocks) == 1 else 'ies'}, "
        f"{seconds / 60:.0f} min"
    )
    conn.execute(
        """INSERT INTO digests (key, title, minutes, seconds, audio_path,
                                pieces, theme, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (key, title, minutes, seconds, str(dest),
         json.dumps([asdict(p) for p in chosen]), theme,
         stamp.isoformat(timespec="seconds")),
    )
    conn.commit()
    return {"key": key, "title": title, "seconds": seconds, "theme": theme,
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
