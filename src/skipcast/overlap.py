"""Spotting the same story twice.

Subscribe to five shows in one field and they converge. A founder does a press
tour and says the same three things on each stop; a market event gets covered
by everyone the same week. The library knows this — it has the topics and the
entities of every episode — but nothing was looking.

## How the comparison is made, and what it is not

This is lexical, not semantic. Two topics are judged related by what they name
in common: the significant words of their titles, and the specifics the
summariser pulled out of each. There is no embedding model here, and adding one
would be the honest way to make this better — it would catch "the chip selloff"
against "semiconductor correction", which this will not.

What it does catch is the case that actually dominates a real library: the same
named things — a company, a person, a figure, an event — discussed inside a few
days of each other. That is most of the redundancy, and it costs no new
dependency, no model call, and nothing at index time.

Scoring is deliberately conservative. Sharing one common word is nothing;
sharing two named entities plus a title term is a story. Better to miss a
repeat than to tell someone two unrelated topics are the same.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# Words that carry no signal about what a topic is about. Deliberately short:
# an aggressive stoplist starts removing the words that distinguish topics.
STOP = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "for", "to", "is",
    "are", "was", "were", "be", "with", "at", "by", "from", "as", "it", "its",
    "this", "that", "these", "those", "vs", "versus", "about", "into", "over",
    "new", "why", "how", "what", "when", "who", "more", "than", "up", "down",
}

_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# Two topics are reported as the same story when they clear both of these.
MIN_SHARED_ENTITIES = 2
MIN_SCORE = 0.34


@dataclass
class Related:
    episode_key: str
    episode_title: str
    feed_slug: str
    feed_title: str
    published_ts: int
    topic_title: str
    topic_position: int
    start_seconds: float | None
    score: float
    shared: list[str]


def terms(text: str) -> set[str]:
    """Significant words of a title, casefolded."""
    return {
        w.casefold() for w in _WORD.findall(text or "")
        if len(w) > 2 and w.casefold() not in STOP
    }


def _topic_entities(conn: sqlite3.Connection) -> dict[int, set[str]]:
    """Entity values falling inside each topic's span, keyed by topic id.

    A specific is attached to a topic by where it was said. Entities with no
    timestamp attach to every topic in their episode — they belong to the
    episode and there is nothing better to say about where.
    """
    topics = conn.execute(
        "SELECT id, episode_id, start_seconds, end_seconds FROM topics"
    ).fetchall()
    by_episode: dict[int, list] = {}
    for t in topics:
        by_episode.setdefault(t["episode_id"], []).append(t)

    out: dict[int, set[str]] = {t["id"]: set() for t in topics}
    for row in conn.execute(
        "SELECT episode_id, value_norm, at_seconds FROM entities"
    ):
        for t in by_episode.get(row["episode_id"], []):
            at, start, end = row["at_seconds"], t["start_seconds"], t["end_seconds"]
            if at is None or start is None:
                out[t["id"]].add(row["value_norm"])
            elif start <= at and (end is None or at < end):
                out[t["id"]].add(row["value_norm"])
    return out


def _score(a_terms: set[str], b_terms: set[str],
           a_ents: set[str], b_ents: set[str]) -> tuple[float, list[str]]:
    """How much two topics have in common, and what.

    Entities count for more than title words: two topics both naming Anthropic
    and $1.5 billion are the same story, where two both containing "market" are
    not. The denominator is the smaller side, so a short topic matching part of
    a long one still scores — a five-minute segment can be the same story as an
    hour of it.
    """
    shared_ents = a_ents & b_ents
    shared_terms = a_terms & b_terms
    if len(shared_ents) < MIN_SHARED_ENTITIES and len(shared_terms) < 3:
        return 0.0, []
    ent_base = min(len(a_ents), len(b_ents)) or 1
    term_base = min(len(a_terms), len(b_terms)) or 1
    score = 0.7 * (len(shared_ents) / ent_base) + 0.3 * (len(shared_terms) / term_base)
    shared = sorted(shared_ents)[:6] or sorted(shared_terms)[:6]
    return round(score, 3), shared


def for_episode(conn: sqlite3.Connection, episode_key: str,
                days: int = 21, limit: int = 5) -> dict[int, list[Related]]:
    """Related topics in *other* episodes, keyed by this episode's topic position.

    Restricted to a window around this episode's publication: the same words a
    year apart are a subject a show returns to, not a story being repeated at
    you this week.
    """
    ep = conn.execute(
        "SELECT id, key, published_ts FROM episodes WHERE key = ?", (episode_key,)
    ).fetchone()
    if ep is None:
        return {}

    window = days * 86400
    mine = conn.execute(
        "SELECT * FROM topics WHERE episode_id = ? ORDER BY position", (ep["id"],)
    ).fetchall()
    if not mine:
        return {}

    others = conn.execute(
        """SELECT t.*, e.key AS episode_key, e.title AS episode_title,
                  e.published_ts, f.slug AS feed_slug, f.title AS feed_title
             FROM topics t
             JOIN episodes e ON e.id = t.episode_id
             JOIN feeds f ON f.id = e.feed_id
            WHERE t.episode_id != ?
              AND ABS(COALESCE(e.published_ts, 0) - ?) <= ?""",
        (ep["id"], ep["published_ts"] or 0, window),
    ).fetchall()
    if not others:
        return {}

    ents = _topic_entities(conn)
    out: dict[int, list[Related]] = {}
    for t in mine:
        a_terms, a_ents = terms(t["title"]), ents.get(t["id"], set())
        hits = []
        for o in others:
            score, shared = _score(a_terms, terms(o["title"]),
                                   a_ents, ents.get(o["id"], set()))
            if score < MIN_SCORE:
                continue
            hits.append(Related(
                episode_key=o["episode_key"],
                episode_title=o["episode_title"] or "",
                feed_slug=o["feed_slug"] or "",
                feed_title=o["feed_title"] or "",
                published_ts=int(o["published_ts"] or 0),
                topic_title=o["title"],
                topic_position=o["position"],
                start_seconds=o["start_seconds"],
                score=score,
                shared=shared,
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        if hits:
            out[t["position"]] = hits[:limit]
    return out


def across_library(conn: sqlite3.Connection, days: int = 21,
                   limit: int = 40) -> list[dict]:
    """Clusters of topics that are the same story, newest first.

    One entry per cluster rather than per pair — being told A matches B and B
    matches A is the same fact twice.
    """
    rows = conn.execute(
        """SELECT t.id, t.title, t.position, t.start_seconds, e.key AS episode_key,
                  e.title AS episode_title, e.published_ts,
                  f.slug AS feed_slug, f.title AS feed_title
             FROM topics t
             JOIN episodes e ON e.id = t.episode_id
             JOIN feeds f ON f.id = e.feed_id
            ORDER BY e.published_ts DESC, t.position"""
    ).fetchall()
    ents = _topic_entities(conn)
    window = days * 86400

    seen: set[int] = set()
    clusters = []
    for i, a in enumerate(rows):
        if a["id"] in seen:
            continue
        a_terms, a_ents = terms(a["title"]), ents.get(a["id"], set())
        members = []
        for b in rows[i + 1:]:
            if b["id"] in seen or b["episode_key"] == a["episode_key"]:
                continue
            if abs((a["published_ts"] or 0) - (b["published_ts"] or 0)) > window:
                continue
            score, shared = _score(a_terms, terms(b["title"]),
                                   a_ents, ents.get(b["id"], set()))
            if score >= MIN_SCORE:
                members.append((b, score, shared))
        if not members:
            continue
        seen.add(a["id"])
        for b, _, _ in members:
            seen.add(b["id"])
        clusters.append({
            "title": a["title"],
            "shows": [
                {"episode_key": a["episode_key"], "episode_title": a["episode_title"],
                 "feed_slug": a["feed_slug"], "feed_title": a["feed_title"],
                 "topic": a["title"], "start_seconds": a["start_seconds"],
                 "published_ts": a["published_ts"], "score": 1.0},
                *[
                    {"episode_key": b["episode_key"],
                     "episode_title": b["episode_title"],
                     "feed_slug": b["feed_slug"], "feed_title": b["feed_title"],
                     "topic": b["title"], "start_seconds": b["start_seconds"],
                     "published_ts": b["published_ts"], "score": score}
                    for b, score, _ in members
                ],
            ],
            "shared": members[0][2],
            "count": len(members) + 1,
        })
        if len(clusters) >= limit:
            break
    return clusters
