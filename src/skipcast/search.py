"""Full-text search over stored transcripts.

The transcripts were already being written and then read exactly once, by the
summariser. This makes them queryable: "when did anyone talk about margin
calls", answered across every episode ever processed, with the speaker, the
timestamp, and a link straight into the audio.

SQLite's FTS5 does the work. The index is derived data — it can always be
rebuilt from the transcript files — so a schema change here drops and rebuilds
rather than migrating.

## Passages, not turns

A diarized turn can run two minutes. Indexing one row per turn would answer
"where was this said" with the moment the speaker *started talking*, which on a
monologue is minutes early. Long turns are therefore split into passages at
sentence boundaries, and each passage's timing is interpolated across the
turn by character position. Speech rate is near enough constant for that to
land within a few seconds, which is the accuracy a jump link needs.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

# Bumped when the indexed shape changes; a mismatch rebuilds from the
# transcripts on disk rather than attempting a migration.
INDEX_VERSION = 1

MAX_PASSAGE_SECONDS = 45.0
MAX_PASSAGE_CHARS = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
    text,
    episode_key UNINDEXED,
    speaker     UNINDEXED,
    start       UNINDEXED,
    end         UNINDEXED,
    tokenize = 'porter unicode61'
);
"""

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)*\*?", re.UNICODE)

# snippet() wraps the matched terms for us, but transcript text is arbitrary and
# may contain < or &. Marking with control characters keeps the highlight out of
# the escaping question entirely: escape first, then swap these for tags.
MARK_OPEN = "\x02"
MARK_CLOSE = "\x03"


class SearchUnavailable(RuntimeError):
    """This SQLite build has no FTS5. Everything else still works."""


@dataclass
class Hit:
    episode_key: str
    speaker: str
    start: float          # seconds into the *original* audio
    end: float
    snippet: str          # matched terms wrapped in MARK_OPEN/MARK_CLOSE
    text: str
    score: float
    episode_title: str = ""
    feed_slug: str = ""
    feed_title: str = ""
    published_ts: int = 0


def available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)"
        )
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the index, rebuilding it if it was written by an older version."""
    if not available(conn):
        raise SearchUnavailable(
            "this SQLite build has no FTS5, so transcript search cannot run. "
            "Everything else in skipcast is unaffected."
        )
    conn.executescript(SCHEMA)
    row = conn.execute(
        "SELECT value FROM search_meta WHERE key = 'index_version'"
    ).fetchone()
    if row and str(row[0]) == str(INDEX_VERSION):
        return
    conn.execute("DROP TABLE IF EXISTS transcript_fts")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO search_meta (key, value) VALUES ('index_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(INDEX_VERSION),),
    )
    conn.commit()


# ---- indexing --------------------------------------------------------------
def passages(turn: dict) -> list[tuple[float, float, str]]:
    """Split one turn into (start, end, text) pieces short enough to jump to."""
    text = (turn.get("text") or "").strip()
    if not text:
        return []
    start = float(turn["start"])
    end = float(turn["end"])
    span = max(0.0, end - start)
    if span <= MAX_PASSAGE_SECONDS and len(text) <= MAX_PASSAGE_CHARS:
        return [(start, end, text)]

    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE.split(text):
        if current and len(current) + len(sentence) + 1 > MAX_PASSAGE_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence
    if current:
        chunks.append(current)

    # Interpolate timings by character position. The transcript keeps only
    # turn-level timings — word timings are consumed when words are regrouped
    # into turns — so proportion of characters is the best signal available.
    total = sum(len(c) for c in chunks) or 1
    out: list[tuple[float, float, str]] = []
    consumed = 0
    for chunk in chunks:
        a = start + span * (consumed / total)
        consumed += len(chunk)
        b = start + span * (consumed / total)
        out.append((round(a, 2), round(b, 2), chunk))
    return out


def drop_episode(conn: sqlite3.Connection, episode_key: str) -> int:
    cur = conn.execute(
        "DELETE FROM transcript_fts WHERE episode_key = ?", (episode_key,)
    )
    return cur.rowcount


def index_episode(conn: sqlite3.Connection, episode_key: str,
                  transcript: dict) -> int:
    """Replace this episode's rows with passages from the given transcript."""
    ensure_schema(conn)
    drop_episode(conn, episode_key)
    rows = []
    for turn in transcript.get("turns") or []:
        speaker = turn.get("speaker_name") or turn.get("speaker_label") or "?"
        for start, end, text in passages(turn):
            # A turn with nothing but punctuation would only ever be noise.
            if not _TOKEN.search(text):
                continue
            rows.append((text, episode_key, speaker, start, end))
    conn.executemany(
        "INSERT INTO transcript_fts (text, episode_key, speaker, start, end) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def index_file(conn: sqlite3.Connection, episode_key: str, path: str | Path) -> int:
    import json

    return index_episode(conn, episode_key, json.loads(Path(path).read_text()))


def prune(conn: sqlite3.Connection) -> int:
    """Drop rows for episodes that no longer exist.

    Unsubscribing cascades the episode rows away; the index sits outside that
    foreign key, so it has to be told.
    """
    ensure_schema(conn)
    cur = conn.execute(
        "DELETE FROM transcript_fts WHERE episode_key NOT IN "
        "(SELECT key FROM episodes)"
    )
    conn.commit()
    return cur.rowcount


def stats(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    passages_n = conn.execute("SELECT COUNT(*) FROM transcript_fts").fetchone()[0]
    episodes_n = conn.execute(
        "SELECT COUNT(DISTINCT episode_key) FROM transcript_fts"
    ).fetchone()[0]
    return {"passages": passages_n, "episodes": episodes_n}


def indexed_keys(conn: sqlite3.Connection) -> set[str]:
    ensure_schema(conn)
    return {
        r[0] for r in conn.execute("SELECT DISTINCT episode_key FROM transcript_fts")
    }


# ---- querying --------------------------------------------------------------
def prepare(query: str) -> str:
    """Turn what someone typed into something FTS5 will accept.

    Raw input goes straight to MATCH only when it is quoted, which is how you
    ask for a phrase. Everything else is tokenised and each term quoted, so an
    apostrophe, a hyphen or a stray AND is searched for rather than parsed as
    syntax. A trailing * still means prefix.
    """
    q = (query or "").strip()
    if not q:
        return ""
    if '"' in q:
        return q
    terms = []
    for token in _TOKEN.findall(q):
        if token.endswith("*"):
            terms.append(f'"{token[:-1]}"*')
        else:
            terms.append(f'"{token}"')
    return " ".join(terms)


def to_html(snippet: str) -> str:
    """Escape a snippet for display, keeping the match highlighted."""
    from html import escape

    return (escape(snippet)
            .replace(MARK_OPEN, "<b>")
            .replace(MARK_CLOSE, "</b>"))


def search(conn: sqlite3.Connection, query: str, limit: int = 40,
           feed_slug: str | None = None, speaker: str | None = None,
           episode_key: str | None = None) -> list[Hit]:
    """Ranked passages matching the query, best first."""
    ensure_schema(conn)
    match = prepare(query)
    if not match:
        return []

    sql = """
        SELECT f.episode_key, f.speaker, f.start, f.end, f.text,
               snippet(transcript_fts, 0, char(2), char(3), '…', 14) AS snip,
               bm25(transcript_fts) AS score,
               e.title AS episode_title, e.published_ts AS published_ts,
               d.slug AS feed_slug, d.title AS feed_title
          FROM transcript_fts f
          LEFT JOIN episodes e ON e.key = f.episode_key
          LEFT JOIN feeds d ON d.id = e.feed_id
         WHERE transcript_fts MATCH ?
    """
    params: list = [match]
    if feed_slug:
        sql += " AND d.slug = ?"
        params.append(feed_slug)
    if speaker:
        sql += " AND f.speaker = ?"
        params.append(speaker)
    if episode_key:
        sql += " AND f.episode_key = ?"
        params.append(episode_key)
    # bm25() returns a negative number, better matches more negative.
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        # A malformed advanced query (unbalanced quote, bare NEAR) lands here.
        raise ValueError(f"could not parse that search: {exc}") from exc

    return [
        Hit(
            episode_key=r["episode_key"],
            speaker=r["speaker"],
            start=float(r["start"]),
            end=float(r["end"]),
            snippet=r["snip"],
            text=r["text"],
            score=float(r["score"]),
            episode_title=r["episode_title"] or "",
            feed_slug=r["feed_slug"] or "",
            feed_title=r["feed_title"] or "",
            published_ts=int(r["published_ts"] or 0),
        )
        for r in rows
    ]
