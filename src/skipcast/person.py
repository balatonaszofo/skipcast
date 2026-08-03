"""Person feeds — one voice, every show they turn up on.

The database has held durable cross-show voice profiles since Phase 1. This is
what they were for. Point a feed at a speaker instead of at a URL, and every
episode any subscription has processed gets reduced to just that person and
served as its own podcast.

Two things make it work that nothing else in the pipeline had to solve:

- **Identity across shows.** A profile learned on one podcast has to match the
  same voice on another, through a different microphone and a different codec.
  That is why profiles are stored per source episode and matched best-of —
  see the schema note in db.py.
- **The inverse edit.** Naming who to keep rather than who to cut, which
  cut.build_plan does with keep_only=. Its own ceiling, because removing 90%
  of an episode is the job here rather than a symptom of a bad match.

Everything here is derived. The audio can be thrown away and rebuilt from the
retained source and its segments, which is why a rebuild is cheap and a person
feed costs no extra download, diarization or transcription.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import audio, cut as cutter, db, identity
from .config import Config


class PersonError(RuntimeError):
    pass


@dataclass
class Appearance:
    key: str
    episode_title: str
    feed_slug: str
    status: str            # ready | skipped | failed
    talk_seconds: float = 0.0
    seconds: float = 0.0
    detail: str = ""


@dataclass
class BuildReport:
    appearances: list[Appearance] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for a in self.appearances if a.status == status)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def person_key(slug: str, episode_key: str) -> str:
    """Stable public id for a derived episode, independent of row ids."""
    return "p" + hashlib.sha1(f"{slug}\n{episode_key}".encode()).hexdigest()[:11]


def person_dir(cfg: Config, slug: str) -> Path:
    return cfg.data_dir / "persons" / slug


def create(conn, cfg: Config, name: str, slug: str | None = None,
           min_seconds: float = 120.0) -> dict:
    """Point a new feed at a known speaker."""
    from . import feeds

    row = conn.execute("SELECT id, name FROM speakers WHERE name = ?",
                       (name,)).fetchone()
    if row is None:
        known = [s.name for s in db.list_speakers(conn)]
        raise PersonError(
            f"no speaker named '{name}'. Known: {', '.join(known) or 'nobody yet'}.\n"
            "Name a voice by labelling an episode, or teach one directly with: "
            "skipcast enroll \"<name>\" <clip>"
        )
    profiles = conn.execute(
        "SELECT COUNT(*) AS n FROM profiles WHERE speaker_id = ?", (row["id"],)
    ).fetchone()["n"]
    if not profiles:
        raise PersonError(
            f"'{name}' has no voice profiles, so there is nothing to match against."
        )

    slug = slug or feeds.slugify(name)
    clash = conn.execute(
        "SELECT speaker_id FROM person_feeds WHERE slug = ?", (slug,)
    ).fetchone()
    if clash and clash["speaker_id"] != row["id"]:
        slug = f"{slug}-{row['id']}"
    if db.get_feed(conn, slug) is not None:
        # A person feed and a podcast feed are served on different routes, but
        # sharing a slug would still be a trap when reading the two lists.
        slug = f"{slug}-person"

    pid = db.add_person_feed(conn, slug, row["id"], row["name"], min_seconds)
    return {"id": pid, "slug": slug, "speaker": row["name"],
            "min_seconds": min_seconds, "profiles": profiles}


def _same_recording(row) -> tuple:
    """Key for spotting one episode reached through two subscriptions.

    Not the guid: a show and a mirror of it (a YouTube rip, someone else's
    re-cut) carry different guids for the same recording, and this is exactly
    the feed where that shows up — subscribe to both and every appearance
    arrives twice. Title plus length to the second is decisive in practice;
    two genuinely different episodes sharing both is not a thing.
    """
    title = " ".join((row["title"] or "").lower().split())
    return title, round(float(row["original_seconds"] or 0))


def _episodes_to_consider(conn) -> tuple[list, list[int]]:
    """Every processed episode that still has what a rebuild needs.

    Returns the episodes to build plus the ids of duplicates that were
    collapsed, so their stale derived rows can be cleared.
    """
    rows = conn.execute(
        """SELECT e.*, f.slug AS feed_slug, f.title AS feed_title
             FROM episodes e JOIN feeds f ON f.id = e.feed_id
            WHERE e.segments_path IS NOT NULL AND e.source_path IS NOT NULL
            ORDER BY e.published_ts DESC, e.id DESC"""
    ).fetchall()

    seen: dict[tuple, object] = {}
    dropped: list[int] = []
    for row in rows:
        marker = _same_recording(row)
        if not marker[0]:                      # untitled: never collapse blindly
            seen[("id", row["id"])] = row
            continue
        winner = seen.get(marker)
        if winner is None:
            seen[marker] = row
            continue
        # Prefer the longest source, which is the least-edited copy of the
        # recording; ties go to whichever was processed first.
        challenger_longer = (float(row["original_seconds"] or 0)
                             > float(winner["original_seconds"] or 0))
        if challenger_longer:
            seen[marker] = row
            dropped.append(winner["id"])
        else:
            dropped.append(row["id"])

    kept = list(seen.values())
    kept.sort(key=lambda r: (-(r["published_ts"] or 0), -r["id"]))
    return kept, dropped


def build(conn, cfg: Config, pf, limit: int | None = None,
          force: bool = False) -> BuildReport:
    """Produce this person's edit of every episode they appear in.

    Idempotent: an appearance already built is left alone unless force is set,
    so this can run after every poll without redoing work.
    """
    report = BuildReport()
    slug = pf["slug"]
    name = pf["speaker"]
    out_dir = person_dir(cfg, slug)
    considered, duplicates = _episodes_to_consider(conn)
    if duplicates:
        # A duplicate may already have been built under an older rule. Clear it,
        # or the feed keeps serving the copy we have since decided against.
        conn.executemany(
            "DELETE FROM person_episodes WHERE person_feed_id = ? AND episode_id = ?",
            [(pf["id"], eid) for eid in duplicates],
        )
        conn.commit()
        _log(f"[person] {slug}: {len(duplicates)} episode(s) reached through more "
             "than one subscription, collapsed to one")
    if limit:
        considered = considered[:limit]
    _log(f"[person] {slug}: {name}, considering {len(considered)} episode(s)")

    for ep in considered:
        key = person_key(slug, ep["key"])
        existing = conn.execute(
            "SELECT * FROM person_episodes WHERE person_feed_id = ? AND episode_id = ?",
            (pf["id"], ep["id"]),
        ).fetchone()
        if existing and not force:
            path = existing["audio_path"]
            if existing["status"] == "ready" and path and Path(path).is_file():
                report.appearances.append(Appearance(
                    key, ep["title"] or "", ep["feed_slug"], "ready",
                    existing["talk_seconds"] or 0, existing["seconds"] or 0,
                    "already built"))
                continue
            if existing["status"] == "skipped":
                # Whether they appear at all does not change without a
                # re-diarization, so this decision stands until forced.
                report.appearances.append(Appearance(
                    key, ep["title"] or "", ep["feed_slug"], "skipped",
                    existing["talk_seconds"] or 0, 0, existing["error"] or ""))
                continue

        try:
            appearance = _build_one(conn, cfg, pf, ep, key, out_dir)
        except Exception as exc:  # noqa: BLE001 — one episode must not stop the run
            db.upsert_person_episode(conn, pf["id"], ep["id"], key, {
                "status": "failed", "error": f"{type(exc).__name__}: {exc}",
            })
            appearance = Appearance(key, ep["title"] or "", ep["feed_slug"],
                                    "failed", detail=str(exc))
            _log(f"[person]   {(ep['title'] or '')[:50]}: {exc}")
        report.appearances.append(appearance)

    db.mark_person_built(conn, pf["id"])
    _log(f"[person] {slug}: {report.count('ready')} ready, "
         f"{report.count('skipped')} without them, {report.count('failed')} failed")
    return report


def _build_one(conn, cfg: Config, pf, ep, key: str, out_dir: Path) -> Appearance:
    name = pf["speaker"]
    title = ep["title"] or "(untitled)"
    doc = json.loads(Path(ep["segments_path"]).read_text())

    # Re-match rather than trusting what the segments file recorded: a voice
    # enrolled or labelled since that episode was processed should light it up
    # without needing the whole pipeline run again.
    identity.annotate(doc, identity.match_document(doc, conn, cfg,
                                                   feed_id=ep["feed_id"]))
    labels = [s["speaker_label"] for s in doc["speakers"]
              if (s.get("matched_name") or "") == name]
    talk = sum(s["total_seconds"] for s in doc["speakers"]
               if s["speaker_label"] in labels)

    if talk < float(pf["min_seconds"]):
        detail = (f"{talk:.0f}s of talk, under the {pf['min_seconds']:.0f}s minimum"
                  if labels else "not in this episode")
        db.upsert_person_episode(conn, pf["id"], ep["id"], key, {
            "talk_seconds": talk, "status": "skipped", "error": detail,
        })
        return Appearance(key, title, ep["feed_slug"], "skipped", talk, 0, detail)

    source = Path(ep["source_path"])
    if not source.is_file():
        raise FileNotFoundError(
            "the source audio is gone; reprocess that episode before building"
        )

    dest = out_dir / f"{key}.mp3"
    cuts_path = out_dir / f"{key}.cuts.json"
    plan = cutter.build_plan(doc, cfg, keep_only=[name])
    cutter.write_log(plan, doc, cfg, cuts_path)
    _log(f"[person]   {title[:50]}: {talk / 60:.1f} min of {name} "
         f"-> {plan.result_seconds / 60:.1f} min")
    cutter.render(source, plan, cfg, dest)

    seconds = audio.duration_seconds(dest)
    db.upsert_person_episode(conn, pf["id"], ep["id"], key, {
        "audio_path": str(dest),
        "cuts_path": str(cuts_path),
        # Measured, not planned — crossfades overlap their joins, and the feed
        # advertises this number.
        "seconds": seconds,
        "talk_seconds": talk,
        "status": "ready",
        "error": None,
    })
    return Appearance(key, title, ep["feed_slug"], "ready", talk, seconds)


def build_all(conn, cfg: Config, force: bool = False) -> dict[str, BuildReport]:
    """Refresh every person feed. What poll calls once new episodes land."""
    out = {}
    for pf in db.list_person_feeds(conn):
        out[pf["slug"]] = build(conn, cfg, pf, force=force)
    return out
