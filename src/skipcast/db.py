"""SQLite state: speaker identities and their voice profiles.

Raw sqlite3 on purpose — single machine, single user, a handful of tables.

Schema note: a speaker gets one profile row *per source episode* rather than a
single running average. Averaging across different shows blurs the vector
toward the middle of every recording chain it has seen and ends up matching
nothing well. Keeping them separate lets matching take the best of several,
which is what makes a profile survive a move to a differently-recorded podcast.
"""

from __future__ import annotations

import array
import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS speakers (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    skip       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id            INTEGER PRIMARY KEY,
    speaker_id    INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    source        TEXT NOT NULL,   -- audio file the embedding came from
    cluster_label TEXT NOT NULL,   -- SPEAKER_06, meaningful only within source
    embedding     BLOB NOT NULL,   -- float64 little-endian
    dim           INTEGER NOT NULL,
    seconds       REAL NOT NULL,   -- talk time backing this embedding
    created_at    TEXT NOT NULL,
    UNIQUE(speaker_id, source, cluster_label)
);

CREATE INDEX IF NOT EXISTS profiles_speaker ON profiles(speaker_id);

CREATE TABLE IF NOT EXISTS feeds (
    id             INTEGER PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    url            TEXT NOT NULL UNIQUE,
    title          TEXT,
    description    TEXT,
    link           TEXT,
    language       TEXT,
    author         TEXT,
    image_url      TEXT,
    added_at       TEXT NOT NULL,
    last_polled_at TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
    id               INTEGER PRIMARY KEY,
    feed_id          INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    -- Stable public identifier, derived from feed url + guid. Used in the
    -- served audio URL so it survives the database being rebuilt.
    key              TEXT NOT NULL UNIQUE,
    guid             TEXT NOT NULL,
    title            TEXT,
    description      TEXT,
    link             TEXT,
    published        TEXT,          -- RFC 822, as the source feed gave it
    published_ts     INTEGER,       -- for ordering
    enclosure_url    TEXT,          -- the original CDN url, kept for reference
    enclosure_type   TEXT,
    source_path      TEXT,          -- our own downloaded copy
    segments_path    TEXT,
    cut_path         TEXT,
    cuts_path        TEXT,
    original_seconds REAL,
    result_seconds   REAL,
    cut_seconds      REAL,
    cut_speakers     TEXT,          -- comma separated names actually removed
    status           TEXT NOT NULL, -- pending|ready|failed|refused
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(feed_id, guid)
);

CREATE INDEX IF NOT EXISTS episodes_feed ON episodes(feed_id, published_ts DESC);
"""


@dataclass
class Speaker:
    id: int
    name: str
    skip: bool
    profile_count: int = 0
    total_seconds: float = 0.0


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def db_path(cfg: Config) -> Path:
    return cfg.data_dir / "skipcast.db"


def connect(cfg: Config, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the state database, creating it if needed.

    Pass check_same_thread=False for the labelling server, whose request
    handlers run on worker threads. Callers that do so must serialise their own
    access — see the lock in labeler.serve.
    """
    path = db_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def pack(vec: list[float]) -> bytes:
    return array.array("d", vec).tobytes()


def unpack(blob: bytes) -> list[float]:
    a = array.array("d")
    a.frombytes(blob)
    return list(a)


def get_or_create_speaker(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("speaker name cannot be empty")
    row = conn.execute("SELECT id FROM speakers WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO speakers (name, skip, created_at) VALUES (?, 0, ?)", (name, _now())
    )
    conn.commit()
    return cur.lastrowid


def add_profile(
    conn: sqlite3.Connection,
    speaker_id: int,
    source: str,
    cluster_label: str,
    embedding: list[float],
    seconds: float,
) -> None:
    """Record one voice sample. Re-labelling the same cluster overwrites it."""
    conn.execute(
        """INSERT INTO profiles
               (speaker_id, source, cluster_label, embedding, dim, seconds, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(speaker_id, source, cluster_label) DO UPDATE SET
               embedding = excluded.embedding,
               dim       = excluded.dim,
               seconds   = excluded.seconds,
               created_at= excluded.created_at""",
        (speaker_id, source, cluster_label, pack(embedding), len(embedding),
         seconds, _now()),
    )
    conn.commit()


def drop_profiles_for_source(conn: sqlite3.Connection, source: str) -> int:
    """Clear an episode's labels so it can be re-labelled from scratch."""
    cur = conn.execute("DELETE FROM profiles WHERE source = ?", (source,))
    conn.commit()
    return cur.rowcount


def list_speakers(conn: sqlite3.Connection) -> list[Speaker]:
    rows = conn.execute(
        """SELECT s.id, s.name, s.skip,
                  COUNT(p.id) AS profile_count,
                  COALESCE(SUM(p.seconds), 0) AS total_seconds
             FROM speakers s
             LEFT JOIN profiles p ON p.speaker_id = s.id
            GROUP BY s.id
            ORDER BY s.name COLLATE NOCASE"""
    ).fetchall()
    return [
        Speaker(r["id"], r["name"], bool(r["skip"]), r["profile_count"],
                r["total_seconds"])
        for r in rows
    ]


def all_profiles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT p.id, p.speaker_id, p.source, p.cluster_label, p.embedding,
                  p.seconds, s.name, s.skip
             FROM profiles p JOIN speakers s ON s.id = p.speaker_id"""
    ).fetchall()
    return [
        {
            "speaker_id": r["speaker_id"],
            "name": r["name"],
            "skip": bool(r["skip"]),
            "source": r["source"],
            "cluster_label": r["cluster_label"],
            "embedding": unpack(r["embedding"]),
            "seconds": r["seconds"],
        }
        for r in rows
    ]


# --- feeds and episodes ----------------------------------------------------
def add_feed(conn: sqlite3.Connection, slug: str, url: str, meta: dict) -> int:
    conn.execute(
        """INSERT INTO feeds (slug, url, title, description, link, language,
                              author, image_url, added_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET
               title=excluded.title, description=excluded.description,
               link=excluded.link, language=excluded.language,
               author=excluded.author, image_url=excluded.image_url""",
        (slug, url, meta.get("title"), meta.get("description"), meta.get("link"),
         meta.get("language"), meta.get("author"), meta.get("image_url"), _now()),
    )
    conn.commit()
    return conn.execute("SELECT id FROM feeds WHERE url = ?", (url,)).fetchone()["id"]


def list_feeds(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT f.*,
                  (SELECT COUNT(*) FROM episodes e WHERE e.feed_id = f.id
                    AND e.status='ready') AS ready_count,
                  (SELECT COUNT(*) FROM episodes e WHERE e.feed_id = f.id) AS total_count
             FROM feeds f ORDER BY f.slug"""
    ).fetchall()


def get_feed(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM feeds WHERE slug = ?", (slug,)).fetchone()


def get_episode_by_guid(conn: sqlite3.Connection, feed_id: int, guid: str):
    return conn.execute(
        "SELECT * FROM episodes WHERE feed_id = ? AND guid = ?", (feed_id, guid)
    ).fetchone()


def get_episode_by_key(conn: sqlite3.Connection, key: str):
    return conn.execute("SELECT * FROM episodes WHERE key = ?", (key,)).fetchone()


def upsert_episode(conn: sqlite3.Connection, feed_id: int, key: str, guid: str,
                   fields: dict) -> int:
    """Idempotent on (feed_id, guid) — the whole point of poll being re-runnable."""
    cols = ["title", "description", "link", "published", "published_ts",
            "enclosure_url", "enclosure_type", "source_path", "segments_path",
            "cut_path", "cuts_path", "original_seconds", "result_seconds",
            "cut_seconds", "cut_speakers", "status", "error"]
    present = {k: fields[k] for k in cols if k in fields}
    row = get_episode_by_guid(conn, feed_id, guid)
    if row is None:
        names = ["feed_id", "key", "guid", "created_at", "updated_at", *present]
        values = [feed_id, key, guid, _now(), _now(), *present.values()]
        cur = conn.execute(
            f"INSERT INTO episodes ({','.join(names)}) "
            f"VALUES ({','.join('?' * len(names))})",
            values,
        )
        conn.commit()
        return cur.lastrowid
    if present:
        sets = ", ".join(f"{k} = ?" for k in present)
        conn.execute(
            f"UPDATE episodes SET {sets}, updated_at = ? WHERE id = ?",
            [*present.values(), _now(), row["id"]],
        )
        conn.commit()
    return row["id"]


def feed_episodes(conn: sqlite3.Connection, feed_id: int, ready_only: bool = True):
    sql = "SELECT * FROM episodes WHERE feed_id = ?"
    if ready_only:
        sql += " AND status = 'ready'"
    sql += " ORDER BY published_ts DESC, id DESC"
    return conn.execute(sql, (feed_id,)).fetchall()


def mark_polled(conn: sqlite3.Connection, feed_id: int) -> None:
    conn.execute("UPDATE feeds SET last_polled_at = ? WHERE id = ?", (_now(), feed_id))
    conn.commit()


def set_skip(conn: sqlite3.Connection, name: str, skip: bool) -> bool:
    cur = conn.execute(
        "UPDATE speakers SET skip = ? WHERE name = ?", (1 if skip else 0, name)
    )
    conn.commit()
    return cur.rowcount > 0


def forget(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("DELETE FROM speakers WHERE name = ?", (name,))
    conn.commit()
    return cur.rowcount > 0
