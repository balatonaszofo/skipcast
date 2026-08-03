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
    interstitial_seconds REAL,      -- of cut_seconds, how much was ads etc
    cut_speakers     TEXT,          -- comma separated names actually removed
    transcript_path  TEXT,
    summary_path     TEXT,
    summary_json_path TEXT,        -- the machine-readable half of the summary
    summary_model    TEXT,
    status           TEXT NOT NULL, -- pending|ready|failed|refused
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(feed_id, guid)
);

CREATE INDEX IF NOT EXISTS episodes_feed ON episodes(feed_id, published_ts DESC);

-- Per-feed exceptions to speakers.skip. A voice you cut from one show is
-- usually a voice you want kept when they turn up as a guest somewhere else,
-- and the global flag alone cannot express that. Absence of a row means "use
-- the global flag", which is why this holds overrides rather than every
-- decision — subscribing to a new feed must not need N rows written to keep
-- behaving as it did.
CREATE TABLE IF NOT EXISTS speaker_feed_rules (
    speaker_id INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    feed_id    INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    skip       INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (speaker_id, feed_id)
);

-- A feed backed by a person rather than a URL: every appearance of one voice
-- across every subscription, each episode reduced to just them. The rows in
-- person_episodes are derived — the audio can be rebuilt from the source and
-- the segments at any time, which is why they carry no metadata of their own
-- beyond what the build produced.
CREATE TABLE IF NOT EXISTS person_feeds (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    speaker_id  INTEGER NOT NULL UNIQUE REFERENCES speakers(id) ON DELETE CASCADE,
    title       TEXT,
    -- Appearances shorter than this are not worth a feed item. A guest who
    -- says forty seconds is not "on the show".
    min_seconds REAL NOT NULL DEFAULT 120,
    created_at  TEXT NOT NULL,
    built_at    TEXT
);

CREATE TABLE IF NOT EXISTS person_episodes (
    id             INTEGER PRIMARY KEY,
    person_feed_id INTEGER NOT NULL REFERENCES person_feeds(id) ON DELETE CASCADE,
    episode_id     INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    key            TEXT NOT NULL UNIQUE,
    audio_path     TEXT,
    cuts_path      TEXT,
    seconds        REAL,        -- length of the derived audio
    talk_seconds   REAL,        -- their diarized talk time in the source
    status         TEXT NOT NULL,  -- ready | skipped | failed
    error          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(person_feed_id, episode_id)
);

CREATE INDEX IF NOT EXISTS person_episodes_feed
    ON person_episodes(person_feed_id, status);

-- The specifics a summary extracted — tickers, dates, figures, claims —
-- unpacked from the per-episode JSON into rows so they can be asked about
-- across the whole library. Derived data: rebuilt from the .summary.json files
-- by `skipcast index`.
CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY,
    episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,
    value      TEXT NOT NULL,
    value_norm TEXT NOT NULL,      -- casefolded, for matching
    detail     TEXT,
    speaker    TEXT,
    at_seconds REAL,               -- into the original audio
    confidence TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS entities_norm ON entities(value_norm);
CREATE INDEX IF NOT EXISTS entities_episode ON entities(episode_id);

-- Terms worth being told about. seen_at is what makes "new since last time"
-- mean anything; without it every check reports the same hits forever.
CREATE TABLE IF NOT EXISTS watchlist (
    id         INTEGER PRIMARY KEY,
    term       TEXT NOT NULL,
    term_norm  TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    seen_at    TEXT
);

-- Where you got to in each episode. Server-side rather than in the browser so
-- your place survives clearing site data and follows you between devices.
CREATE TABLE IF NOT EXISTS playback (
    episode_key TEXT PRIMARY KEY,
    position    REAL NOT NULL DEFAULT 0,
    duration    REAL,
    finished    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
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


# Columns added after the first release. CREATE TABLE IF NOT EXISTS does
# nothing to a table that already exists, so an established database never sees
# them without this. Adding a nullable column is the only migration shape this
# project needs; anything structural rebuilds from the files on disk instead.
ADDED_COLUMNS = {
    "episodes": [("summary_json_path", "TEXT"), ("interstitial_seconds", "REAL")],
}


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring an existing database up to the current schema. Idempotent."""
    applied = []
    for table, columns in ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name in have:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError as exc:
                # Two workers can open the database at the same moment and both
                # find the column missing. Losing that race is success.
                if "duplicate column" not in str(exc).lower():
                    raise
                continue
            applied.append(f"{table}.{name}")
    if applied:
        conn.commit()
    return applied


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
    # Transcription and diarization run on separate workers so they can
    # overlap, which means two writers. The default rollback journal makes them
    # take turns for the whole of every write transaction and fail immediately
    # on contention; WAL lets a reader carry on during a write, and the timeout
    # turns the remaining collisions into a short wait instead of an error.
    # WAL is a property of the database file, so this only does anything once.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.executescript(SCHEMA)
    migrate(conn)
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


# --- per-feed skip rules ---------------------------------------------------
def set_feed_rule(conn: sqlite3.Connection, name: str, feed_id: int,
                  skip: bool | None) -> bool:
    """Override the global skip flag for one speaker on one feed.

    skip=None clears the override, which is not the same as setting it to
    False: cleared means "follow the global flag from now on", False means
    "keep this person here even though they are cut everywhere else".
    """
    row = conn.execute("SELECT id FROM speakers WHERE name = ?", (name,)).fetchone()
    if row is None:
        return False
    if skip is None:
        conn.execute(
            "DELETE FROM speaker_feed_rules WHERE speaker_id = ? AND feed_id = ?",
            (row["id"], feed_id),
        )
    else:
        conn.execute(
            """INSERT INTO speaker_feed_rules (speaker_id, feed_id, skip, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(speaker_id, feed_id) DO UPDATE SET skip = excluded.skip""",
            (row["id"], feed_id, 1 if skip else 0, _now()),
        )
    conn.commit()
    return True


def feed_rules(conn: sqlite3.Connection, feed_id: int | None) -> dict[int, bool]:
    """Per-speaker overrides for one feed, keyed by speaker id."""
    if feed_id is None:
        return {}
    return {
        r["speaker_id"]: bool(r["skip"])
        for r in conn.execute(
            "SELECT speaker_id, skip FROM speaker_feed_rules WHERE feed_id = ?",
            (feed_id,),
        )
    }


def rules_by_feed(conn: sqlite3.Connection) -> list[dict]:
    """Every override, for display."""
    return [
        {"speaker": r["name"], "speaker_id": r["speaker_id"], "feed_id": r["feed_id"],
         "slug": r["slug"], "feed_title": r["feed_title"], "skip": bool(r["skip"])}
        for r in conn.execute(
            """SELECT r.speaker_id, r.feed_id, r.skip, s.name,
                      f.slug, f.title AS feed_title
                 FROM speaker_feed_rules r
                 JOIN speakers s ON s.id = r.speaker_id
                 JOIN feeds f ON f.id = r.feed_id
                ORDER BY f.slug, s.name COLLATE NOCASE"""
        )
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
            "cut_seconds", "interstitial_seconds", "cut_speakers",
            "transcript_path", "summary_path",
            "summary_json_path", "summary_model", "status", "error"]
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


# --- person feeds ----------------------------------------------------------
def add_person_feed(conn: sqlite3.Connection, slug: str, speaker_id: int,
                    title: str, min_seconds: float) -> int:
    conn.execute(
        """INSERT INTO person_feeds (slug, speaker_id, title, min_seconds, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(speaker_id) DO UPDATE SET
               slug = excluded.slug, title = excluded.title,
               min_seconds = excluded.min_seconds""",
        (slug, speaker_id, title, min_seconds, _now()),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM person_feeds WHERE speaker_id = ?", (speaker_id,)
    ).fetchone()["id"]


def list_person_feeds(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT p.*, s.name AS speaker,
                  (SELECT COUNT(*) FROM person_episodes pe
                    WHERE pe.person_feed_id = p.id AND pe.status = 'ready')
                    AS ready_count,
                  (SELECT COALESCE(SUM(pe.seconds), 0) FROM person_episodes pe
                    WHERE pe.person_feed_id = p.id AND pe.status = 'ready')
                    AS total_seconds
             FROM person_feeds p JOIN speakers s ON s.id = p.speaker_id
            ORDER BY p.slug"""
    ).fetchall()


def get_person_feed(conn: sqlite3.Connection, slug: str):
    return conn.execute(
        """SELECT p.*, s.name AS speaker FROM person_feeds p
             JOIN speakers s ON s.id = p.speaker_id WHERE p.slug = ?""",
        (slug,),
    ).fetchone()


def person_episodes(conn: sqlite3.Connection, person_feed_id: int,
                    ready_only: bool = True) -> list[sqlite3.Row]:
    """Derived episodes joined to what they were made from."""
    sql = """SELECT pe.*, e.title, e.guid, e.link, e.published, e.published_ts,
                    e.description, e.original_seconds, f.slug AS feed_slug,
                    f.title AS feed_title
               FROM person_episodes pe
               JOIN episodes e ON e.id = pe.episode_id
               JOIN feeds f ON f.id = e.feed_id
              WHERE pe.person_feed_id = ?"""
    if ready_only:
        sql += " AND pe.status = 'ready'"
    sql += " ORDER BY e.published_ts DESC, pe.id DESC"
    return conn.execute(sql, (person_feed_id,)).fetchall()


def get_person_episode(conn: sqlite3.Connection, key: str):
    return conn.execute("SELECT * FROM person_episodes WHERE key = ?",
                        (key,)).fetchone()


def upsert_person_episode(conn: sqlite3.Connection, person_feed_id: int,
                          episode_id: int, key: str, fields: dict) -> None:
    cols = ["audio_path", "cuts_path", "seconds", "talk_seconds", "status", "error"]
    present = {k: fields[k] for k in cols if k in fields}
    row = conn.execute(
        "SELECT id FROM person_episodes WHERE person_feed_id = ? AND episode_id = ?",
        (person_feed_id, episode_id),
    ).fetchone()
    if row is None:
        names = ["person_feed_id", "episode_id", "key", "created_at", "updated_at",
                 *present]
        values = [person_feed_id, episode_id, key, _now(), _now(), *present.values()]
        conn.execute(
            f"INSERT INTO person_episodes ({','.join(names)}) "
            f"VALUES ({','.join('?' * len(names))})",
            values,
        )
    elif present:
        sets = ", ".join(f"{k} = ?" for k in present)
        conn.execute(
            f"UPDATE person_episodes SET {sets}, updated_at = ? WHERE id = ?",
            [*present.values(), _now(), row["id"]],
        )
    conn.commit()


def mark_person_built(conn: sqlite3.Connection, person_feed_id: int) -> None:
    conn.execute("UPDATE person_feeds SET built_at = ? WHERE id = ?",
                 (_now(), person_feed_id))
    conn.commit()


def remove_person_feed(conn: sqlite3.Connection, slug: str) -> bool:
    cur = conn.execute("DELETE FROM person_feeds WHERE slug = ?", (slug,))
    conn.commit()
    return cur.rowcount > 0


def set_position(conn: sqlite3.Connection, key: str, position: float,
                 duration: float | None, finished: bool) -> None:
    conn.execute(
        """INSERT INTO playback (episode_key, position, duration, finished, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(episode_key) DO UPDATE SET
               position=excluded.position, duration=excluded.duration,
               finished=excluded.finished, updated_at=excluded.updated_at""",
        (key, max(0.0, position), duration, 1 if finished else 0, _now()),
    )
    conn.commit()


def positions(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM playback").fetchall()
    return {
        r["episode_key"]: {"position": r["position"], "duration": r["duration"],
                           "finished": bool(r["finished"])}
        for r in rows
    }


def ready_episodes(conn: sqlite3.Connection, limit: int = 100):
    """Everything playable, newest first, across every feed."""
    return conn.execute(
        """SELECT e.*, f.slug AS feed_slug, f.title AS feed_title
             FROM episodes e JOIN feeds f ON f.id = e.feed_id
            WHERE e.status = 'ready' AND e.cut_path IS NOT NULL
            ORDER BY e.published_ts DESC, e.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()


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
