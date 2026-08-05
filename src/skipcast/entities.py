"""The specifics, across every episode rather than one at a time.

Phase 1 made the summariser hand back its findings in a machine-readable
block. Each of those lived in its own file, which answers "what was in this
episode" and nothing else. This unpacks them into rows, so the library can
answer the questions the per-episode view cannot:

    when did anyone last talk about NVDA
    tell me when someone mentions Ozempic

That second one is the watchlist. It is deliberately not a notification
system — nothing here sends anything anywhere. It records what is worth being
told about and what you have already seen, and the control panel shows the
difference.

A term can also point the other way: `skip` means cut the chapters that are
about it. Watching and skipping are one list with two directions, so the rest
of this module talks about *terms* and their *state* rather than about a
watchlist.

Everything is derived from the .summary.json files, so the whole table can be
dropped and rebuilt by `skipcast index`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def normalize(value: str) -> str:
    """Casefold and squeeze whitespace — enough to match 'NVDA' to 'nvda'.

    Deliberately not stemming or stripping punctuation: '5.2%' and '$20B' are
    exactly the kind of value that matters here, and normalising them into
    '52' and '20b' would make the index worse, not better.
    """
    return " ".join((value or "").split()).casefold()


@dataclass
class Mention:
    value: str
    type: str
    detail: str
    speaker: str
    confidence: str
    evidence: str
    at_seconds: float | None
    episode_key: str
    episode_title: str
    feed_slug: str
    feed_title: str
    published_ts: int


def index_topics(conn: sqlite3.Connection, episode_id: int, data: dict,
                 duration: float = 0.0) -> int:
    """Replace this episode's topic rows, giving each one an end.

    The summary says where a topic opens and nothing about where it closes, so
    a topic runs until the next one starts and the last runs to the end of the
    episode. That is an assumption, but it is the assumption the summary itself
    is making by listing topics in order — and without an end a topic is not
    something you can extract and listen to.
    """
    conn.execute("DELETE FROM topics WHERE episode_id = ?", (episode_id,))
    topics = [
        t for t in (data.get("topics") or [])
        if (t.get("title") or "").strip()
    ]
    rows = []
    for i, t in enumerate(topics):
        start = t.get("at_seconds")
        end = None
        if start is not None:
            for later in topics[i + 1:]:
                if later.get("at_seconds") is not None:
                    end = later["at_seconds"]
                    break
            if end is None and duration:
                end = duration
            # A topic that opens after the one following it is a bad timestamp,
            # not a zero-length topic; leave the span off rather than invent it.
            if end is not None and end <= start:
                end = None
        rows.append((
            episode_id, i, str(t["title"]).strip()[:200],
            str(t.get("one_line") or "").strip()[:400],
            ", ".join(str(s) for s in (t.get("speakers") or []))[:200],
            start, end, _now(),
        ))
    conn.executemany(
        """INSERT INTO topics (episode_id, position, title, one_line, speakers,
                               start_seconds, end_seconds, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def index_episode(conn: sqlite3.Connection, episode_id: int, data: dict) -> int:
    """Replace this episode's entity rows from a parsed summary index."""
    conn.execute("DELETE FROM entities WHERE episode_id = ?", (episode_id,))
    rows = []
    for s in data.get("specifics") or []:
        value = (s.get("value") or "").strip()
        if not value:
            continue
        rows.append((
            episode_id,
            (s.get("type") or "other").strip().lower(),
            value,
            normalize(value),
            (s.get("detail") or "").strip(),
            (s.get("speaker") or "").strip(),
            s.get("at_seconds"),
            (s.get("confidence") or "").strip(),
            (s.get("evidence") or "").strip(),
            _now(),
        ))
    conn.executemany(
        """INSERT INTO entities (episode_id, type, value, value_norm, detail,
                                 speaker, at_seconds, confidence, evidence,
                                 created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def index_file(conn: sqlite3.Connection, episode_id: int, path: str | Path) -> int:
    return index_episode(conn, episode_id, json.loads(Path(path).read_text()))


def reindex_all(conn: sqlite3.Connection, log=None) -> int:
    """Rebuild entities and topics from every .summary.json on disk."""
    total = 0
    rows = conn.execute(
        "SELECT id, key, summary_json_path, original_seconds FROM episodes "
        "WHERE summary_json_path IS NOT NULL"
    ).fetchall()
    for row in rows:
        path = Path(row["summary_json_path"])
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
            n = index_episode(conn, row["id"], data)
            t = index_topics(conn, row["id"], data,
                             float(row["original_seconds"] or 0))
        except ValueError as exc:
            if log:
                log(f"[entities] {row['key']}: {exc}")
            continue
        total += n
        if log:
            log(f"[entities] {row['key']}: {n} specifics, {t} topics")
    return total


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT episode_id) AS eps FROM entities"
    ).fetchone()
    return {"mentions": row["n"], "episodes": row["eps"]}


def _mentions(conn: sqlite3.Connection, where: str, params: list,
              limit: int) -> list[Mention]:
    rows = conn.execute(
        f"""SELECT n.*, e.key AS episode_key, e.title AS episode_title,
                   e.published_ts, f.slug AS feed_slug, f.title AS feed_title
              FROM entities n
              JOIN episodes e ON e.id = n.episode_id
              JOIN feeds f ON f.id = e.feed_id
             WHERE {where}
             ORDER BY e.published_ts DESC, n.at_seconds LIMIT ?""",
        [*params, limit],
    ).fetchall()
    return [
        Mention(
            value=r["value"], type=r["type"], detail=r["detail"] or "",
            speaker=r["speaker"] or "", confidence=r["confidence"] or "",
            evidence=(r["evidence"] or "") if "evidence" in r.keys() else "",
            at_seconds=r["at_seconds"], episode_key=r["episode_key"],
            episode_title=r["episode_title"] or "", feed_slug=r["feed_slug"] or "",
            feed_title=r["feed_title"] or "",
            published_ts=int(r["published_ts"] or 0),
        )
        for r in rows
    ]


def lookup(conn: sqlite3.Connection, term: str = "", kind: str = "",
           limit: int = 50, evidence: str = "") -> list[Mention]:
    """Mentions matching a term, newest episode first.

    Matches the value first and the detail second, so searching "Anthropic"
    finds both the entry named Anthropic and the claim that mentions them in
    passing.
    """
    clauses, params = [], []
    norm = normalize(term)
    if norm:
        clauses.append("(n.value_norm LIKE ? OR LOWER(n.detail) LIKE ?)")
        params += [f"%{norm}%", f"%{norm}%"]
    if kind:
        clauses.append("n.type = ?")
        params.append(kind.strip().lower())
    if evidence:
        want = evidence.strip().lower()
        if want == "any":
            # Everything that was graded at all — the set of claims about the
            # world, as opposed to tickers and dates.
            clauses.append("n.evidence IS NOT NULL AND n.evidence != ''")
        else:
            clauses.append("n.evidence = ?")
            params.append(want)
    return _mentions(conn, " AND ".join(clauses) or "1=1", params, limit)


def types(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    return [
        (r["type"], r["n"]) for r in conn.execute(
            "SELECT type, COUNT(*) AS n FROM entities GROUP BY type "
            "ORDER BY n DESC"
        )
    ]


# ---- watchlist -------------------------------------------------------------
def watch_add(conn: sqlite3.Connection, term: str) -> bool:
    term = (term or "").strip()
    if not term:
        raise ValueError("a term is required")
    conn.execute(
        "INSERT INTO watchlist (term, term_norm, state, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(term_norm) DO UPDATE SET state = 'watch'",
        (term, normalize(term), "watch", _now()),
    )
    conn.commit()
    return True


def watch_remove(conn: sqlite3.Connection, term: str) -> bool:
    cur = conn.execute("DELETE FROM watchlist WHERE term_norm = ?",
                       (normalize(term),))
    conn.commit()
    return cur.rowcount > 0


def watch_list(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM watchlist ORDER BY term COLLATE NOCASE"
    ).fetchall()


def watch_hits(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Each watched term with its mentions, newest first.

    `new` counts what has arrived since the term was last marked seen. A term
    added today does not retroactively announce five years of back catalogue as
    news, so anything indexed before the term existed counts as already seen.
    """
    out = []
    for w in watch_list(conn):
        if state_of(w) != "watch":
            continue  # a skipped term is not something to be told more about
        hits = _mentions(
            conn, "(n.value_norm LIKE ? OR LOWER(n.detail) LIKE ?)",
            [f"%{w['term_norm']}%", f"%{w['term_norm']}%"], limit,
        )
        since = w["seen_at"] or w["created_at"]
        fresh = conn.execute(
            """SELECT COUNT(*) AS n FROM entities n
                WHERE (n.value_norm LIKE ? OR LOWER(n.detail) LIKE ?)
                  AND n.created_at > ?""",
            (f"%{w['term_norm']}%", f"%{w['term_norm']}%", since),
        ).fetchone()["n"]
        out.append({"term": w["term"], "new": fresh, "total": len(hits),
                    "seen_at": w["seen_at"], "mentions": hits})
    return out


def watch_mark_seen(conn: sqlite3.Connection, term: str | None = None) -> int:
    if term:
        cur = conn.execute(
            "UPDATE watchlist SET seen_at = ? WHERE term_norm = ?",
            (_now(), normalize(term)),
        )
    else:
        cur = conn.execute("UPDATE watchlist SET seen_at = ?", (_now(),))
    conn.commit()
    return cur.rowcount


# ---- terms and their state -------------------------------------------------
def set_state(conn: sqlite3.Connection, term: str, state: str | None) -> None:
    """Watch a term, skip it, or hold no opinion (which forgets it).

    Skipping is the only state that removes audio, so it is also the only one
    that can be wrong in a way you would not notice — which is why the caller
    is expected to have shown the impact first.
    """
    term = (term or "").strip()
    if not term:
        raise ValueError("a term is required")
    norm = normalize(term)
    if state is None:
        conn.execute("DELETE FROM watchlist WHERE term_norm = ?", (norm,))
        conn.execute("DELETE FROM topic_feed_rules WHERE term_norm = ?", (norm,))
    else:
        if state not in ("watch", "skip"):
            raise ValueError("state must be watch, skip or nothing")
        conn.execute(
            """INSERT INTO watchlist (term, term_norm, state, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(term_norm) DO UPDATE SET state = excluded.state""",
            (term, norm, state, _now()),
        )
    conn.commit()


def terms(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM watchlist ORDER BY term COLLATE NOCASE"
    ).fetchall()


def state_of(row) -> str:
    """A row's state, reading the pre-state rows as what they were: watched."""
    keys = row.keys() if hasattr(row, "keys") else []
    return (row["state"] if "state" in keys and row["state"] else "watch")


def set_feed_rule(conn: sqlite3.Connection, term: str, feed_id: int,
                  skip: bool | None) -> None:
    """Make one show an exception to a term's global state, or stop being one."""
    norm = normalize(term)
    if skip is None:
        conn.execute(
            "DELETE FROM topic_feed_rules WHERE term_norm = ? AND feed_id = ?",
            (norm, feed_id),
        )
    else:
        conn.execute(
            """INSERT INTO topic_feed_rules (term_norm, feed_id, skip, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(term_norm, feed_id) DO UPDATE SET skip = excluded.skip""",
            (norm, feed_id, 1 if skip else 0, _now()),
        )
    conn.commit()


def feed_rules(conn: sqlite3.Connection) -> list[dict]:
    """Every per-show term exception, joined to names the UI can show."""
    return [
        {"term": r["term"] or r["term_norm"], "term_norm": r["term_norm"],
         "slug": r["slug"], "feed_title": r["feed_title"], "skip": bool(r["skip"])}
        for r in conn.execute(
            """SELECT r.term_norm, r.skip, f.slug, f.title AS feed_title,
                      w.term
                 FROM topic_feed_rules r
                 JOIN feeds f ON f.id = r.feed_id
                 LEFT JOIN watchlist w ON w.term_norm = r.term_norm"""
        )
    ]


def skip_terms_for_feed(conn: sqlite3.Connection, feed_id: int) -> list[tuple[str, str]]:
    """(display term, normalised term) to cut from this feed's episodes.

    A global skip applies unless this show says keep; a show can also skip a
    term nothing is skipping globally.
    """
    rules = {
        r["term_norm"]: bool(r["skip"]) for r in conn.execute(
            "SELECT term_norm, skip FROM topic_feed_rules WHERE feed_id = ?",
            (feed_id,),
        )
    }
    out = []
    seen = set()
    for row in terms(conn):
        norm = row["term_norm"]
        wanted = rules.get(norm, state_of(row) == "skip")
        if wanted:
            out.append((row["term"], norm))
        seen.add(norm)
    # A rule can name a term that carries no global row of its own.
    for norm, skip in rules.items():
        if skip and norm not in seen:
            out.append((norm, norm))
    return out


def term_matcher(term_norm: str):
    """Match a term as whole words, never as a substring.

    "AI" must not match "Ukraine" and "NBA" must not match "NBAgate". The
    watchlist can afford LIKE '%term%' because a false positive there only
    shows you an extra line; here a false positive deletes audio, so the bar
    is higher. Terms that begin or end with punctuation ("$20B") lose the
    boundary on that side, where it would never match anything anyway.
    """
    esc = re.escape(term_norm)
    left = r"\b" if term_norm[:1].isalnum() else ""
    right = r"\b" if term_norm[-1:].isalnum() else ""
    return re.compile(left + esc + right).search


def _episode_topic_text(conn: sqlite3.Connection,
                        episode_id: int) -> dict[int, str]:
    """Each topic's searchable text: its title plus the specifics said inside it.

    Entities with no timestamp belong to the episode rather than to any one
    topic, so they are left out — a chapter should be cut for what it is
    about, not for what the episode mentioned somewhere else.
    """
    rows = conn.execute(
        "SELECT id, title, one_line, start_seconds, end_seconds FROM topics "
        "WHERE episode_id = ? ORDER BY position",
        (episode_id,),
    ).fetchall()
    text = {r["id"]: normalize(f"{r['title']} {r['one_line'] or ''}") for r in rows}
    ents = conn.execute(
        "SELECT value, detail, at_seconds FROM entities WHERE episode_id = ? "
        "AND at_seconds IS NOT NULL",
        (episode_id,),
    ).fetchall()
    for r in rows:
        start, end = r["start_seconds"], r["end_seconds"]
        if start is None:
            continue
        inside = [
            f"{e['value']} {e['detail'] or ''}" for e in ents
            if start <= e["at_seconds"] and (end is None or e["at_seconds"] < end)
        ]
        if inside:
            text[r["id"]] = normalize(text[r["id"]] + " " + " ".join(inside))
    return text


def skipped_chapters(conn: sqlite3.Connection, episode_id: int,
                     feed_id: int) -> list[dict]:
    """Which of this episode's chapters are about something being skipped.

    Only chapters with a real span come back: a chapter with no end cannot be
    cut out, and guessing one would be inventing a boundary the summary never
    claimed.
    """
    wanted = skip_terms_for_feed(conn, feed_id)
    if not wanted:
        return []
    matchers = [(term, term_matcher(norm)) for term, norm in wanted]
    text = _episode_topic_text(conn, episode_id)
    out = []
    for r in conn.execute(
        "SELECT * FROM topics WHERE episode_id = ? ORDER BY position",
        (episode_id,),
    ):
        if r["start_seconds"] is None or r["end_seconds"] is None:
            continue
        hits = [term for term, search in matchers if search(text.get(r["id"], ""))]
        if hits:
            out.append({
                "topic_id": r["id"], "position": r["position"],
                "title": r["title"], "one_line": r["one_line"] or "",
                "start_seconds": float(r["start_seconds"]),
                "end_seconds": float(r["end_seconds"]),
                "seconds": float(r["end_seconds"]) - float(r["start_seconds"]),
                "terms": hits,
            })
    return out


def skip_impact(conn: sqlite3.Connection, term: str,
                unplayed_only: bool = False) -> list[dict]:
    """What skipping this term would remove, across every processed episode.

    Answered before anything is cut. It is the same matching the cut itself
    uses, so what the preview lists is exactly what goes.
    """
    search = term_matcher(normalize(term))
    out = []
    for ep in conn.execute(
        """SELECT e.id, e.key, e.title, e.feed_id, e.published_ts,
                  f.slug AS feed_slug, f.title AS feed_title,
                  COALESCE(p.finished, 0) AS finished
             FROM episodes e
             JOIN feeds f ON f.id = e.feed_id
             LEFT JOIN playback p ON p.episode_key = e.key
            WHERE e.status = 'ready'
            ORDER BY e.published_ts DESC"""
    ):
        if unplayed_only and ep["finished"]:
            continue
        text = _episode_topic_text(conn, ep["id"])
        for r in conn.execute(
            "SELECT * FROM topics WHERE episode_id = ? ORDER BY position",
            (ep["id"],),
        ):
            if r["start_seconds"] is None or r["end_seconds"] is None:
                continue
            if not search(text.get(r["id"], "")):
                continue
            out.append({
                "episode_key": ep["key"], "episode_title": ep["title"] or "",
                "feed_slug": ep["feed_slug"], "feed_title": ep["feed_title"] or "",
                "published_ts": int(ep["published_ts"] or 0),
                "title": r["title"], "at_seconds": float(r["start_seconds"]),
                "seconds": float(r["end_seconds"]) - float(r["start_seconds"]),
            })
    return out


# The bar for calling something a recurring segment. Two episodes is enough
# evidence, because the discriminating test is not how often a phrase appears
# but *where*: a segment is something the show names, and a name leads the
# title — "Science Corner: Fruit Fly Brains". A subject that merely comes up
# twice ("...World Models, and AGI Timeline") or a two-part series on one story
# ("...and Mary Vetsera's erasure") repeats words in the middle of a title, and
# is not something to offer to delete. The share test is the second guard: a
# segment is a slice of an episode, not most of it.
RECUR_MIN_EPISODES = 2
RECUR_MIN_SHARE = 0.3
RECUR_MAX_TOPIC_SHARE = 0.34
RECUR_MIN_NAME_WORDS = 2
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def recurring_segments(conn: sqlite3.Connection, feed_id: int) -> list[dict]:
    """Segments this show runs most weeks, as one-tap skip suggestions.

    Grouped by the significant words their titles share, so "Science Corner:
    Fruit Fly Brains" and "Science Corner: Fusion" are one segment. The
    suggested term is the shared part, which is the part that will still match
    next week.
    """
    from .overlap import STOP

    rows = conn.execute(
        """SELECT t.id, t.title, t.episode_id, t.start_seconds, t.end_seconds
             FROM topics t JOIN episodes e ON e.id = t.episode_id
            WHERE e.feed_id = ? AND e.status = 'ready'""",
        (feed_id,),
    ).fetchall()
    episodes = {r["episode_id"] for r in rows}
    if len(episodes) < RECUR_MIN_EPISODES:
        return []

    def sig(title: str) -> list[str]:
        """Significant words of a title, keeping the original spelling."""
        return [w for w in _WORD.findall(title or "")
                if len(w) > 2 and w.casefold() not in STOP]

    # Group by the significant words a title *opens* with — the segment name.
    groups: dict[tuple, list] = {}
    for r in rows:
        words = sig(r["title"])
        if len(words) < RECUR_MIN_NAME_WORDS:
            continue
        key = tuple(w.casefold() for w in words[:RECUR_MIN_NAME_WORDS])
        groups.setdefault(key, []).append((r, words))

    per_episode: dict[int, int] = {}
    for r in rows:
        per_episode[r["episode_id"]] = per_episode.get(r["episode_id"], 0) + 1

    out = []
    for members in groups.values():
        eps = {r["episode_id"] for r, _ in members}
        if len(eps) < RECUR_MIN_EPISODES or len(eps) / len(episodes) < RECUR_MIN_SHARE:
            continue
        # A segment is a slice of an episode. A group that is most of what an
        # episode covers is the episode's subject, not an interruption in it.
        share = sum(
            sum(1 for r, _ in members if r["episode_id"] == ep) / per_episode[ep]
            for ep in eps
        ) / len(eps)
        if share > RECUR_MAX_TOPIC_SHARE:
            continue
        # The name is however far the titles agree from the start, which is
        # usually longer than the two words that grouped them.
        lists = [w for _, w in members]
        phrase = []
        for i in range(min(len(w) for w in lists)):
            if len({w[i].casefold() for w in lists}) != 1:
                break
            phrase.append(lists[0][i])
        if len(phrase) < RECUR_MIN_NAME_WORDS:
            continue
        spans = [float(r["end_seconds"]) - float(r["start_seconds"])
                 for r, _ in members
                 if r["start_seconds"] is not None and r["end_seconds"] is not None]
        out.append({
            "term": " ".join(phrase),
            "episodes": len(eps),
            "of_episodes": len(episodes),
            "avg_seconds": round(sum(spans) / len(spans), 1) if spans else 0.0,
            "examples": [r["title"] for r, _ in members[:3]],
        })
    out.sort(key=lambda s: s["episodes"], reverse=True)
    return out
