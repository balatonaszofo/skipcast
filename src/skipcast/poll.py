"""`skipcast poll` — fetch new episodes and run the whole pipeline on them.

Idempotent on episode GUID: an episode already marked ready is skipped
entirely, and a failed one is retried on the next run. Each stage records its
output path in the database as it completes, so a run interrupted halfway
resumes rather than starting over.
"""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import audio
from . import cut as cutter
from . import db, feeds, identity
from .config import Config


@dataclass
class Outcome:
    key: str
    title: str
    status: str          # ready | failed | refused | skipped
    detail: str = ""


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)


def _episode_dir(cfg: Config) -> Path:
    return cfg.data_dir / "episodes"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def process_entry(conn, cfg: Config, feed_row, entry: feeds.Entry,
                  force: bool = False) -> Outcome:
    key = feeds.episode_key(feed_row["url"], entry.guid)
    existing = db.get_episode_by_guid(conn, feed_row["id"], entry.guid)

    if existing and existing["status"] == "ready" and not force:
        cut_path = existing["cut_path"]
        if cut_path and Path(cut_path).is_file():
            return Outcome(key, entry.title, "skipped", "already processed")
        # Row says ready but the audio is gone; fall through and rebuild.
        _log(f"[poll] {entry.title[:60]}: cut file missing, rebuilding")

    db.upsert_episode(conn, feed_row["id"], key, entry.guid, {
        "title": entry.title,
        "description": entry.description,
        "link": entry.link,
        "published": entry.published,
        "published_ts": entry.published_ts,
        "enclosure_url": entry.enclosure_url,
        "enclosure_type": entry.enclosure_type,
        "status": "pending",
        "error": None,
    })

    ep_dir = _episode_dir(cfg)
    suffix = Path(entry.enclosure_url.split("?")[0]).suffix or ".mp3"
    source = ep_dir / f"{key}.source{suffix}"
    segments_path = ep_dir / f"{key}.segments.json"
    cut_path = ep_dir / f"{key}.cut.mp3"
    cuts_path = ep_dir / f"{key}.cuts.json"

    try:
        _log(f"[poll] {entry.title[:70]}")

        # 1. our own copy, downloaded once
        if not source.is_file() or force:
            _log("[poll]   downloading")
            feeds.download_enclosure(entry.enclosure_url, source)
        else:
            _log("[poll]   already downloaded")

        # 2. diarize + match against known voices
        if segments_path.is_file() and not force:
            _log("[poll]   reusing existing diarization")
            doc = json.loads(segments_path.read_text())
        else:
            from .diarize import diarize_file

            _log("[poll]   diarizing")
            doc = diarize_file(source, cfg, {
                "title": entry.title,
                "source_url": entry.link or entry.enclosure_url,
                "uploader": feed_row["title"],
            })
        matches = identity.match_document(doc, conn, cfg)
        identity.annotate(doc, matches)
        segments_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

        unmatched = [
            s for s in doc["speakers"]
            if not s.get("matched_name") and s["total_seconds"] > 120
        ]
        if unmatched:
            _log(f"[poll]   {len(unmatched)} substantial cluster(s) unrecognised "
                 f"— they will not be cut")

        db.upsert_episode(conn, feed_row["id"], key, entry.guid, {
            "source_path": str(source),
            "segments_path": str(segments_path),
            "original_seconds": doc["duration"],
        })

        # 3. cut
        plan = cutter.build_plan(doc, cfg)
        cutter.write_log(plan, doc, cfg, cuts_path)

        names = sorted({
            (next(s for s in doc["speakers"] if s["speaker_label"] == label)
             .get("matched_name") or label)
            for label in plan.skipped_labels
        })
        if plan.cuts:
            _log(f"[poll]   cutting {plan.cut_seconds / 60:.1f} min "
                 f"({plan.fraction * 100:.0f}%) of {', '.join(names)}")
        else:
            _log("[poll]   nothing flagged to cut — re-encoding unchanged")

        cutter.render(source, plan, cfg, cut_path)

        db.upsert_episode(conn, feed_row["id"], key, entry.guid, {
            "cut_path": str(cut_path),
            "cuts_path": str(cuts_path),
            # Measured, not planned: each crossfade overlaps its join, so the
            # encoded file is shorter than the sum of the kept pieces. The feed
            # advertises this number, so it has to be the real one.
            "result_seconds": audio.duration_seconds(cut_path),
            "cut_seconds": plan.cut_seconds,
            "cut_speakers": ", ".join(names),
            "status": "ready",
            "error": None,
        })
        return Outcome(key, entry.title, "ready",
                       f"{plan.cut_seconds / 60:.1f} min removed")

    except cutter.CutRefused as exc:
        # Deliberately not a failure to retry forever: the rules said no.
        db.upsert_episode(conn, feed_row["id"], key, entry.guid,
                          {"status": "refused", "error": str(exc)})
        return Outcome(key, entry.title, "refused", str(exc))
    except Exception as exc:  # noqa: BLE001 — one bad episode must not stop the run
        db.upsert_episode(conn, feed_row["id"], key, entry.guid,
                          {"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        _log(traceback.format_exc().strip().splitlines()[-1])
        return Outcome(key, entry.title, "failed", f"{type(exc).__name__}: {exc}")


def entry_from_row(row) -> feeds.Entry:
    """Rebuild a feed entry from a stored episode, for reprocessing."""
    return feeds.Entry(
        guid=row["guid"],
        title=row["title"] or "(untitled)",
        description=row["description"] or "",
        link=row["link"] or "",
        published=row["published"] or "",
        published_ts=row["published_ts"] or 0,
        enclosure_url=row["enclosure_url"] or "",
        enclosure_type=row["enclosure_type"] or "audio/mpeg",
        duration=None,
    )


def recut_episode(conn, cfg: Config, ep) -> Outcome:
    """Re-apply the cut rules to an already-diarized episode.

    Changing who is flagged skip, or a cut parameter, does not need the
    download or the diarization again — those are the expensive halves. This
    re-matches identities against the current database and re-renders.
    """
    key = ep["key"]
    segments_path = Path(ep["segments_path"] or "")
    source = Path(ep["source_path"] or "")
    if not segments_path.is_file() or not source.is_file():
        raise FileNotFoundError(
            "need both the source audio and its segments file to recut; "
            "reprocess this episode instead"
        )

    doc = json.loads(segments_path.read_text())
    identity.annotate(doc, identity.match_document(doc, conn, cfg))
    segments_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    cut_path = Path(ep["cut_path"] or (_episode_dir(cfg) / f"{key}.cut.mp3"))
    cuts_path = Path(ep["cuts_path"] or (_episode_dir(cfg) / f"{key}.cuts.json"))

    plan = cutter.build_plan(doc, cfg)
    cutter.write_log(plan, doc, cfg, cuts_path)
    names = sorted({
        (next(s for s in doc["speakers"] if s["speaker_label"] == label)
         .get("matched_name") or label)
        for label in plan.skipped_labels
    })
    _log(f"[recut] {ep['title'][:60]}: {plan.cut_seconds / 60:.1f} min "
         f"({plan.fraction * 100:.0f}%) of {', '.join(names) or 'nobody'}")
    cutter.render(source, plan, cfg, cut_path)

    db.upsert_episode(conn, ep["feed_id"], key, ep["guid"], {
        "cut_path": str(cut_path),
        "cuts_path": str(cuts_path),
        "result_seconds": audio.duration_seconds(cut_path),
        "cut_seconds": plan.cut_seconds,
        "cut_speakers": ", ".join(names),
        "status": "ready",
        "error": None,
    })
    return Outcome(key, ep["title"] or "", "ready", f"{plan.cut_seconds / 60:.1f} min removed")


def poll_feed(conn, cfg: Config, feed_row, limit: int | None = None,
              force: bool = False) -> Report:
    report = Report()
    _log(f"[poll] {feed_row['slug']}: reading {feed_row['url']}")
    try:
        meta, entries = feeds.parse(feed_row["url"])
    except Exception:
        # Record the attempt even when the feed is unreadable. Without this the
        # scheduler sees a feed that has never been polled and re-queues it on
        # every pass, turning one broken feed into a failure loop.
        db.mark_polled(conn, feed_row["id"])
        raise
    db.add_feed(conn, feed_row["slug"], feed_row["url"], meta)

    limit = cfg.poll.max_episodes if limit is None else limit
    considered = entries[:limit] if limit > 0 else entries
    _log(f"[poll] {len(entries)} episodes in feed, considering newest {len(considered)}")

    for entry in considered:
        outcome = process_entry(conn, cfg, feed_row, entry, force=force)
        report.outcomes.append(outcome)
        if report.count("failed") >= cfg.poll.max_failures:
            _log(f"[poll] stopping after {report.count('failed')} failures")
            break

    db.mark_polled(conn, feed_row["id"])
    return report
