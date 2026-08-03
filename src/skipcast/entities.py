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

Everything is derived from the .summary.json files, so the whole table can be
dropped and rebuilt by `skipcast index`.
"""

from __future__ import annotations

import datetime as dt
import json
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
    at_seconds: float | None
    episode_key: str
    episode_title: str
    feed_slug: str
    feed_title: str
    published_ts: int


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
            _now(),
        ))
    conn.executemany(
        """INSERT INTO entities (episode_id, type, value, value_norm, detail,
                                 speaker, at_seconds, confidence, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def index_file(conn: sqlite3.Connection, episode_id: int, path: str | Path) -> int:
    return index_episode(conn, episode_id, json.loads(Path(path).read_text()))


def reindex_all(conn: sqlite3.Connection, log=None) -> int:
    """Rebuild from every .summary.json on disk."""
    total = 0
    rows = conn.execute(
        "SELECT id, key, summary_json_path FROM episodes "
        "WHERE summary_json_path IS NOT NULL"
    ).fetchall()
    for row in rows:
        path = Path(row["summary_json_path"])
        if not path.is_file():
            continue
        try:
            n = index_file(conn, row["id"], path)
        except ValueError as exc:
            if log:
                log(f"[entities] {row['key']}: {exc}")
            continue
        total += n
        if log:
            log(f"[entities] {row['key']}: {n} specifics")
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
            at_seconds=r["at_seconds"], episode_key=r["episode_key"],
            episode_title=r["episode_title"] or "", feed_slug=r["feed_slug"] or "",
            feed_title=r["feed_title"] or "",
            published_ts=int(r["published_ts"] or 0),
        )
        for r in rows
    ]


def lookup(conn: sqlite3.Connection, term: str = "", kind: str = "",
           limit: int = 50) -> list[Mention]:
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
        "INSERT INTO watchlist (term, term_norm, created_at) VALUES (?,?,?) "
        "ON CONFLICT(term_norm) DO NOTHING",
        (term, normalize(term), _now()),
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
