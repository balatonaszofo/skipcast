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

import contextlib
import datetime as dt
import io
import json
import queue
import sqlite3
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,      -- poll | reprocess | recut
    target      TEXT,               -- feed slug or episode key
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


@dataclass
class Worker:
    cfg: Config
    _thread: threading.Thread | None = None
    _wake: queue.Queue | None = None
    _stop: threading.Event | None = None
    _last_write: float = 0.0

    def start(self) -> None:
        from . import db

        conn = db.connect(self.cfg, check_same_thread=False)
        ensure_schema(conn)
        # Anything left running from a previous process died with it.
        conn.execute(
            "UPDATE jobs SET status='failed', error='interrupted by restart', "
            "finished_at=? WHERE status='running'", (_now(),),
        )
        conn.commit()
        conn.close()

        self._wake = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="skipcast-worker",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._stop:
            self._stop.set()
        if self._wake:
            self._wake.put(None)

    def poke(self) -> None:
        """Tell the worker a job was just queued."""
        if self._wake:
            self._wake.put(None)

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
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                conn.close()
                try:
                    self._wake.get(timeout=5)
                except queue.Empty:
                    pass
                continue

            job_id = row["id"]
            conn.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?",
                         (_now(), job_id))
            conn.commit()
            conn.close()

            sink = _LogSink(self, job_id)
            status, error = "done", None
            try:
                with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
                    self._dispatch(row, sink)
            except Exception as exc:  # noqa: BLE001 — a bad job must not kill the worker
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"
                sink.lines.extend(traceback.format_exc().strip().splitlines()[-6:])

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
                poller.poll_feed(conn, self.cfg, feed,
                                 limit=params.get("limit"),
                                 force=params.get("force", False))

            elif row["kind"] == "reprocess":
                ep = db.get_episode_by_key(conn, row["target"])
                if ep is None:
                    raise ValueError(f"no episode {row['target']}")
                feed = conn.execute("SELECT * FROM feeds WHERE id=?",
                                    (ep["feed_id"],)).fetchone()
                entry = poller.entry_from_row(ep)
                poller.process_entry(conn, self.cfg, feed, entry, force=True)

            elif row["kind"] == "summarize":
                ep = db.get_episode_by_key(conn, row["target"])
                if ep is None:
                    raise ValueError(f"no episode {row['target']}")
                poller.transcribe_and_summarize(
                    conn, self.cfg, ep, force=params.get("force", False)
                )

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
_scheduler: Scheduler | None = None


def worker_for(cfg: Config) -> Worker:
    global _worker, _scheduler
    if _worker is None:
        _worker = Worker(cfg)
        _worker.start()
        _scheduler = Scheduler(cfg, _worker)
        _scheduler.start()
    return _worker
