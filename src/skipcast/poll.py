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
                  force: bool = False, defer_transcript: bool = False) -> Outcome:
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
        matches = identity.match_document(doc, conn, cfg, feed_id=feed_row["id"])
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

        # Transcript and summary come after the episode is already playable, so
        # a failure here never costs you the audio.
        if cfg.transcribe.enabled or cfg.summary.enabled:
            row = db.get_episode_by_guid(conn, feed_row["id"], entry.guid)
            if defer_transcript:
                # Hand it to the slow lane and get on with the next episode.
                # Transcription is CPU-bound and diarization is not, so the two
                # overlap rather than queueing behind each other.
                from . import jobs

                jobs.enqueue(conn, "summarize", key,
                             f"Transcribe {(entry.title or '')[:40]}")
                _log("[poll]   queued transcription")
            else:
                transcribe_and_summarize(conn, cfg, row)

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


def index_transcript(conn, key: str, transcript: dict) -> None:
    """Make an episode searchable. Never fatal — the audio is the deliverable."""
    from . import search

    try:
        n = search.index_episode(conn, key, transcript)
        _log(f"[poll]   indexed {n} passages for search")
    except search.SearchUnavailable as exc:
        _log(f"[poll]   {exc}")
    except Exception as exc:  # noqa: BLE001
        _log(f"[poll]   could not index transcript: {exc}")


CONFIDENCE_ORDER = {"unsure": 0, "likely": 1, "certain": 2}


def selected_interstitials(cfg: Config, data: dict) -> list[dict]:
    """The detected ranges this config is willing to act on."""
    if not cfg.interstitial.enabled:
        return []
    wanted = {k.strip().lower() for k in cfg.interstitial.remove}
    floor = CONFIDENCE_ORDER.get(cfg.interstitial.min_confidence.lower(), 1)
    return [
        i for i in (data or {}).get("interstitials") or []
        if i.get("kind") in wanted
        and CONFIDENCE_ORDER.get(i.get("confidence", "unsure"), 0) >= floor
    ]


def apply_interstitials(conn, cfg: Config, ep, data: dict) -> float:
    """Re-cut an episode with its ad reads and housekeeping removed.

    This runs after the episode is already playable, because the ranges come
    from the transcript and the transcript comes from the audio. So it is a
    second pass over an existing cut rather than part of the first one — which
    also means it is optional, restartable, and cannot cost you the episode if
    it fails.
    """
    ranges = selected_interstitials(cfg, data)
    if not ranges:
        return 0.0

    segments_path = Path(ep["segments_path"] or "")
    source = Path(ep["source_path"] or "")
    if not segments_path.is_file() or not source.is_file():
        return 0.0

    total = sum(r["seconds"] for r in ranges)
    duration = float(ep["original_seconds"] or 0) or 1.0
    if total / duration > cfg.interstitial.max_fraction:
        _log(f"[poll]   {total / 60:.1f} min of interstitials is "
             f"{total / duration * 100:.0f}% of the episode, over the "
             f"{cfg.interstitial.max_fraction * 100:.0f}% ceiling — leaving them in")
        return 0.0

    doc = json.loads(segments_path.read_text())
    identity.annotate(doc, identity.match_document(doc, conn, cfg,
                                                   feed_id=ep["feed_id"]))
    key = ep["key"]
    cut_path = Path(ep["cut_path"] or (_episode_dir(cfg) / f"{key}.cut.mp3"))
    cuts_path = Path(ep["cuts_path"] or (_episode_dir(cfg) / f"{key}.cuts.json"))

    try:
        plan = cutter.build_plan(doc, cfg, extra_cuts=ranges)
    except cutter.CutRefused as exc:
        _log(f"[poll]   not removing interstitials: {exc}")
        return 0.0

    kinds = ", ".join(sorted({r["kind"] for r in ranges}))
    _log(f"[poll]   re-cutting to drop {total / 60:.1f} min of {kinds}")
    cutter.write_log(plan, doc, cfg, cuts_path)
    cutter.render(source, plan, cfg, cut_path)

    db.upsert_episode(conn, ep["feed_id"], key, ep["guid"], {
        "cut_path": str(cut_path),
        "cuts_path": str(cuts_path),
        "result_seconds": audio.duration_seconds(cut_path),
        "cut_seconds": plan.cut_seconds,
        "interstitial_seconds": plan.interstitial_seconds,
    })
    return plan.interstitial_seconds


def index_entities(conn, episode_id: int, data: dict) -> None:
    """Unpack a summary's specifics into the cross-episode index."""
    from . import entities

    try:
        n = entities.index_episode(conn, episode_id, data)
        _log(f"[poll]   indexed {n} specifics")
    except Exception as exc:  # noqa: BLE001 — the summary itself is already saved
        _log(f"[poll]   could not index specifics: {exc}")


def reindex_transcripts(conn, cfg: Config, only_missing: bool = False) -> int:
    """Index every transcript on disk. Safe to re-run.

    The index is derived data, so this is the repair path for all of it: a
    machine that gained FTS5, a database restored without it, or transcripts
    produced before search existed.
    """
    from . import search
    from . import transcribe as stt

    done = search.indexed_keys(conn) if only_missing else set()
    rows = conn.execute(
        "SELECT key, transcript_path FROM episodes "
        "WHERE transcript_path IS NOT NULL ORDER BY published_ts DESC"
    ).fetchall()
    total = indexed = 0
    for row in rows:
        path = Path(row["transcript_path"] or "")
        if not path.is_file() or (only_missing and row["key"] in done):
            continue
        try:
            n = search.index_episode(conn, row["key"], stt.load(path))
        except Exception as exc:  # noqa: BLE001 — one bad file, not the run
            _log(f"[index] {row['key']}: {exc}")
            continue
        total += n
        indexed += 1
        _log(f"[index] {row['key']}: {n} passages")
    _log(f"[index] {total} passages across {indexed} episode(s)")
    return total


def transcribe_and_summarize(conn, cfg: Config, ep, force: bool = False,
                             resummarize: bool = False) -> None:
    """Transcribe an episode and summarise it. Never fatal to the episode.

    Both stages are optional extras on top of a working cut, so a Whisper crash
    or an API outage marks the episode's summary missing rather than failing an
    episode whose audio is already fine.

    `force` redoes both halves. `resummarize` redoes only the summary, reusing
    the transcript — which is what you want after a prompt change, since the
    transcript has not changed and re-running Whisper over 90 minutes of audio
    to get a differently-worded summary is an hour of CPU for nothing.
    """
    from . import summarize as summarizer
    from . import transcribe as stt

    key = ep["key"]
    segments_path = Path(ep["segments_path"] or "")
    source = Path(ep["source_path"] or "")
    if not segments_path.is_file() or not source.is_file():
        return

    ep_dir = _episode_dir(cfg)
    transcript_path = ep_dir / f"{key}.transcript.json"
    summary_path = ep_dir / f"{key}.summary.md"
    summary_json_path = ep_dir / f"{key}.summary.json"
    doc = json.loads(segments_path.read_text())

    if cfg.transcribe.enabled:
        try:
            if transcript_path.is_file() and not force:
                _log("[poll]   reusing existing transcript")
                transcript = stt.load(transcript_path)
            else:
                transcript = stt.transcribe_file(source, doc, cfg)
                transcript_path.write_text(json.dumps(transcript, indent=2),
                                           encoding="utf-8")
            db.upsert_episode(conn, ep["feed_id"], key, ep["guid"],
                              {"transcript_path": str(transcript_path)})
        except Exception as exc:  # noqa: BLE001 — an extra, not the deliverable
            _log(f"[poll]   transcription failed, skipping summary: {exc}")
            return
    elif transcript_path.is_file():
        transcript = stt.load(transcript_path)
    else:
        return

    index_transcript(conn, key, transcript)

    if not cfg.summary.enabled:
        return
    if not summarizer.available(cfg):
        _log(f"[poll]   {summarizer.missing_key_message(cfg.summary.provider)}")
        return
    if summary_path.is_file() and not (force or resummarize):
        _log("[poll]   reusing existing summary")
        db.upsert_episode(conn, ep["feed_id"], key, ep["guid"], {
            "summary_path": str(summary_path),
            "summary_json_path": (str(summary_json_path)
                                  if summary_json_path.is_file() else None),
        })
        return

    cut_note = ""
    if cfg.summary.scope == "original" and ep["cut_speakers"]:
        cut_note = (
            f"Summarise the whole episode, including the parts removed from the "
            f"listener's edited copy ({ep['cut_speakers']}). The listener will "
            f"not hear those parts, so cover them so they know what they skipped."
        )
    # Show title and description are what let the model work out the genre.
    feed = conn.execute("SELECT title, description FROM feeds WHERE id = ?",
                        (ep["feed_id"],)).fetchone()
    try:
        _log(f"[poll]   summarising with {cfg.summary.provider}")
        result = summarizer.summarize(
            stt.as_text(transcript), ep["title"] or "(untitled)", cfg,
            show=feed["title"] if feed else None,
            show_description=feed["description"] if feed else None,
            note=cut_note,
        )
        summary_path.write_text(result.markdown, encoding="utf-8")
        # Timestamps are stored as the summariser gave them: seconds into the
        # original audio. Mapping them onto the edit happens on read, because a
        # recut moves every one of them and stored positions would go stale.
        if result.data:
            summary_json_path.write_text(json.dumps(result.data, indent=2),
                                         encoding="utf-8")
        db.upsert_episode(conn, ep["feed_id"], key, ep["guid"], {
            "summary_path": str(summary_path),
            "summary_json_path": (str(summary_json_path)
                                  if result.data else None),
            "summary_model": result.model,
        })
        if result.data:
            index_entities(conn, ep["id"], result.data)
            try:
                # Refetch: the summary write above changed the row this needs.
                fresh = db.get_episode_by_key(conn, key)
                apply_interstitials(conn, cfg, fresh, result.data)
            except Exception as exc:  # noqa: BLE001 — the cut we have is fine
                _log(f"[poll]   could not remove interstitials: {exc}")
    except Exception as exc:  # noqa: BLE001
        _log(f"[poll]   summary failed: {exc}")


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
    identity.annotate(doc,
                      identity.match_document(doc, conn, cfg,
                                              feed_id=ep["feed_id"]))
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
              force: bool = False, defer_transcript: bool = False) -> Report:
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
        outcome = process_entry(conn, cfg, feed_row, entry, force=force,
                                defer_transcript=defer_transcript)
        report.outcomes.append(outcome)
        if report.count("failed") >= cfg.poll.max_failures:
            _log(f"[poll] stopping after {report.count('failed')} failures")
            break

    db.mark_polled(conn, feed_row["id"])

    # New episodes may contain someone a person feed is following. Building is
    # idempotent and skips what it has already done, so this only costs
    # anything when there is genuinely a new appearance to cut.
    if report.count("ready"):
        try:
            from . import person

            if db.list_person_feeds(conn):
                person.build_all(conn, cfg)
        except Exception as exc:  # noqa: BLE001 — never fail a poll over this
            _log(f"[poll] person feeds could not be refreshed: {exc}")

    return report
