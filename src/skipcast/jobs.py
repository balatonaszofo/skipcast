"""Background jobs, so the phone can start work that takes a quarter of an hour.

Polling an episode means downloading ~100 MB, diarizing slower than realtime,
and re-encoding. No HTTP request can hold that open, so the UI submits a job
and then watches it.

A single worker thread runs jobs one at a time. Serial on purpose: diarization
saturates the GPU, so two at once finish no sooner and just contend. The queue
lives in SQLite alongside everything else — no message queue, per the project's
constraints.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import queue
import sqlite3
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,      -- poll|reprocess|summarize|recut|reindex|person
    target      TEXT,               -- feed slug, episode key, or person slug
    label       TEXT,               -- human description for the UI
    params      TEXT,               -- json
    status      TEXT NOT NULL,      -- queued | running | done | failed | cancelled
    progress    TEXT,
    log         TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, id);
"""

MAX_LOG_LINES = 300


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def enqueue(conn: sqlite3.Connection, kind: str, target: str | None,
            label: str, params: dict | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO jobs (kind, target, label, params, status, created_at)
           VALUES (?,?,?,?, 'queued', ?)""",
        (kind, target, label, json.dumps(params or {}), _now()),
    )
    conn.commit()
    return cur.lastrowid


def get(conn: sqlite3.Connection, job_id: int):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def recent(conn: sqlite3.Connection, limit: int = 20):
    return conn.execute(
        "SELECT id, kind, target, label, status, progress, error, created_at, "
        "started_at, finished_at FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def active(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id"
    ).fetchall()


def cancel(conn: sqlite3.Connection, job_id: int) -> bool:
    """Only a queued job can be cancelled; a running one owns the GPU."""
    cur = conn.execute(
        "UPDATE jobs SET status='cancelled', finished_at=? "
        "WHERE id=? AND status='queued'",
        (_now(), job_id),
    )
    conn.commit()
    return cur.rowcount > 0


class _LogSink(io.TextIOBase):
    """Collects pipeline output into the job row.

    pyannote draws progress bars with carriage returns; they are collapsed so
    one bar does not become three hundred log lines.
    """

    def __init__(self, worker: "Worker", job_id: int):
        self.worker = worker
        self.job_id = job_id
        self.lines: list[str] = []
        self._partial = ""
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            self._partial += text
            # Treat \r like \n so progress bars land as (replaced) single lines.
            while True:
                idx = min(
                    (i for i in (self._partial.find("\n"), self._partial.find("\r"))
                     if i >= 0), default=-1,
                )
                if idx < 0:
                    break
                line, sep = self._partial[:idx], self._partial[idx]
                self._partial = self._partial[idx + 1:]
                line = line.rstrip()
                if not line:
                    continue
                if sep == "\r" and self.lines:
                    self.lines[-1] = line     # in-place progress update
                else:
                    self.lines.append(line)
                if len(self.lines) > MAX_LOG_LINES:
                    del self.lines[: len(self.lines) - MAX_LOG_LINES]
        self.worker._touch(self.job_id, self.snapshot(), self.last())
        return len(text)

    def flush(self) -> None:
        pass

    def last(self) -> str:
        return self.lines[-1] if self.lines else ""

    def snapshot(self) -> str:
        return "\n".join(self.lines)


class _Router:
    """A stdout/stderr stand-in that sends each thread's writes to its own sink.

    Jobs capture their output by rebinding the process streams, which is fine
    with one worker and wrong with two: contextlib.redirect_stdout is global,
    so the moment two jobs run at once each one's log swallows the other's
    lines — and anything the main thread prints disappears into whichever job
    happens to be running.

    Routing per thread keeps the capture but scopes it to the job that asked
    for it. Threads with no sink registered write through to the real stream.
    """

    def __init__(self, real):
        self._real = real
        self._local = threading.local()

    def bind(self, sink) -> None:
        self._local.sink = sink

    def release(self) -> None:
        self._local.sink = None

    @property
    def _target(self):
        return getattr(self._local, "sink", None) or self._real

    def write(self, text: str) -> int:
        return self._target.write(text)

    def flush(self) -> None:
        self._target.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name):
        return getattr(self._real, name)


def _install_routers() -> tuple[_Router, _Router]:
    """Swap the process streams for routers, once."""
    if not isinstance(sys.stdout, _Router):
        sys.stdout = _Router(sys.stdout)
    if not isinstance(sys.stderr, _Router):
        sys.stderr = _Router(sys.stderr)
    return sys.stdout, sys.stderr


# Transcription is CPU-bound and slow; diarization is GPU-bound and not. Run
# them on separate workers and a five-episode poll overlaps the two instead of
# waiting for each transcript before fetching the next episode. The split is by
# job kind, so nothing else has to know about it.
SLOW_KINDS = ("summarize",)


@dataclass
class Worker:
    cfg: Config
    # "main" takes everything except SLOW_KINDS; "slow" takes only those.
    lane: str = "main"
    _thread: threading.Thread | None = None
    _wake: queue.Queue | None = None
    _stop: threading.Event | None = None
    _last_write: float = 0.0
    _twin: "Worker | None" = None

    def start(self, reset_running: bool = True) -> None:
        from . import db

        conn = db.connect(self.cfg, check_same_thread=False)
        ensure_schema(conn)
        if reset_running:
            # Anything left running from a previous process died with it.
            conn.execute(
                "UPDATE jobs SET status='failed', error='interrupted by restart', "
                "finished_at=? WHERE status='running'", (_now(),),
            )
            conn.commit()
        conn.close()

        self._wake = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run,
                                        name=f"skipcast-worker-{self.lane}",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._stop:
            self._stop.set()
        if self._wake:
            self._wake.put(None)

    def poke(self) -> None:
        """Tell the worker a job was just queued, and its twin lane too."""
        if self._wake:
            self._wake.put(None)
        twin = getattr(self, "_twin", None)
        if twin is not None and twin._wake:
            twin._wake.put(None)

    # -- internals ---------------------------------------------------------
    def _touch(self, job_id: int, log: str, progress: str) -> None:
        import time

        # Writing on every line would hammer SQLite during a progress bar.
        now = time.monotonic()
        if now - self._last_write < 0.5:
            return
        self._last_write = now
        from . import db

        try:
            conn = db.connect(self.cfg, check_same_thread=False)
            conn.execute("UPDATE jobs SET log=?, progress=? WHERE id=?",
                         (log, progress[:300], job_id))
            conn.commit()
            conn.close()
        except Exception:  # noqa: BLE001 — logging must never kill the job
            pass

    def _run(self) -> None:
        from . import db

        while not self._stop.is_set():
            conn = db.connect(self.cfg, check_same_thread=False)
            ensure_schema(conn)
            placeholders = ",".join("?" * len(SLOW_KINDS))
            test = "IN" if self.lane == "slow" else "NOT IN"
            row = conn.execute(
                f"SELECT * FROM jobs WHERE status='queued' "
                f"AND kind {test} ({placeholders}) ORDER BY id LIMIT 1",
                SLOW_KINDS,
            ).fetchone()
            if row is None:
                conn.close()
                try:
                    self._wake.get(timeout=5)
                except queue.Empty:
                    pass
                continue

            job_id = row["id"]
            # Claim it, guarding against the other lane having taken it first.
            claimed = conn.execute(
                "UPDATE jobs SET status='running', started_at=? "
                "WHERE id=? AND status='queued'",
                (_now(), job_id),
            ).rowcount
            conn.commit()
            conn.close()
            if not claimed:
                continue

            sink = _LogSink(self, job_id)
            status, error = "done", None
            out, err = _install_routers()
            out.bind(sink)
            err.bind(sink)
            try:
                self._dispatch(row, sink)
            except Exception as exc:  # noqa: BLE001 — a bad job must not kill the worker
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"
                sink.lines.extend(traceback.format_exc().strip().splitlines()[-6:])
            finally:
                out.release()
                err.release()

            conn = db.connect(self.cfg, check_same_thread=False)
            conn.execute(
                "UPDATE jobs SET status=?, error=?, log=?, progress=?, finished_at=? "
                "WHERE id=?",
                (status, error, sink.snapshot(), sink.last()[:300], _now(), job_id),
            )
            conn.commit()
            conn.close()

    def _dispatch(self, row, sink: _LogSink) -> None:
        from . import db, poll as poller

        params = json.loads(row["params"] or "{}")
        conn = db.connect(self.cfg, check_same_thread=False)
        try:
            if row["kind"] == "poll":
                feed = db.get_feed(conn, row["target"])
                if feed is None:
                    raise ValueError(f"no feed named {row['target']}")
                # Inside the server there is a second lane to hand the
                # transcript to; the CLI has no worker running, so it does the
                # slow half itself rather than queueing work nothing will pick
                # up.
                poller.poll_feed(conn, self.cfg, feed,
                                 limit=params.get("limit"),
                                 force=params.get("force", False),
                                 defer_transcript=True)

            elif row["kind"] == "reprocess":
                ep = db.get_episode_by_key(conn, row["target"])
                if ep is None:
                    raise ValueError(f"no episode {row['target']}")
                feed = conn.execute("SELECT * FROM feeds WHERE id=?",
                                    (ep["feed_id"],)).fetchone()
                entry = poller.entry_from_row(ep)
                poller.process_entry(conn, self.cfg, feed, entry, force=True,
                                     defer_transcript=True)

            elif row["kind"] == "summarize":
                ep = db.get_episode_by_key(conn, row["target"])
                if ep is None:
                    raise ValueError(f"no episode {row['target']}")
                # Asking for a summary means produce one now, so an existing
                # one is replaced — but the transcript is reused unless the
                # caller explicitly asked for the expensive half again.
                poller.transcribe_and_summarize(
                    conn, self.cfg, ep, force=params.get("force", False),
                    resummarize=True,
                )

            elif row["kind"] == "reindex":
                poller.reindex_transcripts(conn, self.cfg)

            elif row["kind"] == "digest":
                from . import digest as digester

                digester.build(conn, self.cfg,
                               minutes=params.get("minutes", 30),
                               feed_slug=params.get("feed"),
                               unplayed_only=not params.get("include_played"))

            elif row["kind"] == "person":
                from . import db as _db, person

                pf = _db.get_person_feed(conn, row["target"])
                if pf is None:
                    raise ValueError(f"no person feed named {row['target']}")
                person.build(conn, self.cfg, pf,
                             force=params.get("force", False))

            elif row["kind"] == "recut":
                # Re-apply the cut rules using current skip flags, without
                # re-downloading or re-diarizing.
                ep = db.get_episode_by_key(conn, row["target"])
                if ep is None:
                    raise ValueError(f"no episode {row['target']}")
                poller.recut_episode(conn, self.cfg, ep)
            else:
                raise ValueError(f"unknown job kind {row['kind']}")
        finally:
            conn.close()


class Scheduler:
    """Queues a poll for any feed that has not been polled recently.

    Decides from each feed's last_polled_at rather than from a timer, so
    restarting the server does not re-poll everything, and a machine that was
    asleep catches up once instead of once per missed interval.
    """

    def __init__(self, cfg: Config, worker: Worker):
        self.cfg = cfg
        self.worker = worker
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.cfg.poll.interval_hours <= 0:
            return
        self._thread = threading.Thread(target=self._run, name="skipcast-scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _due(self, last_polled: str | None) -> bool:
        if not last_polled:
            return True
        try:
            when = dt.datetime.fromisoformat(last_polled)
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        age = (dt.datetime.now(dt.timezone.utc) - when).total_seconds()
        return age >= self.cfg.poll.interval_hours * 3600

    def _run(self) -> None:
        from . import db

        # Let the server finish coming up before touching anything.
        if self._stop.wait(30):
            return
        while not self._stop.is_set():
            try:
                conn = db.connect(self.cfg, check_same_thread=False)
                ensure_schema(conn)
                pending = {
                    r["target"] for r in active(conn) if r["kind"] == "poll"
                }
                for feed in db.list_feeds(conn):
                    if feed["slug"] in pending:
                        continue  # already queued or running
                    if self._due(feed["last_polled_at"]):
                        enqueue(conn, "poll", feed["slug"],
                                f"Scheduled poll {feed['slug']}", {})
                        self.worker.poke()
                conn.close()
            except Exception:  # noqa: BLE001 — the scheduler must never die
                pass
            self._stop.wait(300)   # re-check every five minutes


_worker: Worker | None = None
_slow_worker: Worker | None = None
_scheduler: Scheduler | None = None


def worker_for(cfg: Config) -> Worker:
    """The main worker, starting the slow lane and the scheduler alongside it.

    Callers only ever talk to the main one — poke() on it wakes both, since a
    job enqueued for either lane should not wait out the other's poll interval.
    """
    global _worker, _slow_worker, _scheduler
    if _worker is None:
        _worker = Worker(cfg, lane="main")
        _worker.start()
        _slow_worker = Worker(cfg, lane="slow")
        # Only one of them may clear running jobs, or the second wipes the
        # first's freshly claimed row.
        _slow_worker.start(reset_running=False)
        _worker._twin = _slow_worker
        _scheduler = Scheduler(cfg, _worker)
        _scheduler.start()
    return _worker
