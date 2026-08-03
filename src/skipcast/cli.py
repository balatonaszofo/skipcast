"""skipcast command line.

Phase 0: analyze only. label / speakers / cut / subscribe / poll / serve land
in later phases.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _cmd_fetch(args: argparse.Namespace) -> int:
    from .audio import FFmpegMissing
    from .config import load_config
    from .fetch import FetchError, fetch, is_url

    cfg = load_config(args.config)
    if not is_url(args.url):
        print(f"error: not a URL: {args.url}", file=sys.stderr)
        return 1
    try:
        got = fetch(args.url, cfg, force=args.force)
    except (FetchError, FFmpegMissing) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"audio:    {got.audio_path}")
    print(f"metadata: {got.meta_path}")
    print(f"analyze:  skipcast analyze '{got.audio_path}'")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    from .audio import FFmpegMissing
    from .config import load_config
    from .diarize import DiarizationError, diarize_file
    from .fetch import FetchError, fetch, is_url, load_meta
    from . import preview

    cfg = load_config(args.config)

    meta = None
    if is_url(args.audio):
        try:
            got = fetch(args.audio, cfg, force=args.refetch)
        except (FetchError, FFmpegMissing) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        src, meta = got.audio_path, got.meta
    else:
        src = Path(args.audio).expanduser().resolve()
        meta = load_meta(src)

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else src.parent
    stem = src.name.rsplit(".", 1)[0]
    segments_path = out_dir / f"{stem}.segments.json"
    preview_path = out_dir / f"{stem}.preview.html"

    if segments_path.exists() and not args.force:
        print(
            f"{segments_path} already exists. Re-rendering the preview from it; "
            "pass --force to re-run diarization.",
            file=sys.stderr,
        )
        doc = json.loads(segments_path.read_text())
    else:
        try:
            doc = diarize_file(src, cfg, meta)
        except (DiarizationError, FFmpegMissing, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        out_dir.mkdir(parents=True, exist_ok=True)
        segments_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    # Match clusters against known voices before writing anything out, so the
    # segments file and the preview both carry the identities.
    from . import db, identity

    conn = db.connect(cfg)
    matches = identity.match_document(doc, conn, cfg)
    identity.annotate(doc, matches)
    conn.close()
    segments_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    # Relative link so the page works as long as the audio stays alongside it.
    import os.path
    from urllib.parse import quote

    try:
        rel = os.path.relpath(src, out_dir)
    except ValueError:
        rel = str(src)
    preview.write(doc, cfg, preview_path, audio_src=quote(rel))

    print()
    print(f"segments: {segments_path}")
    print(f"preview:  {preview_path}")
    print()
    print(f"{len(doc['segments'])} segments, {len(doc['speakers'])} speaker clusters:")
    unknown = 0
    for s in doc["speakers"]:
        mins = s["total_seconds"] / 60
        name = s.get("matched_name")
        if name:
            tag = f"= {name}" + ("  [SKIP]" if s.get("skip") else "")
            tag += f"  (sim {s['similarity']:.3f})"
        else:
            tag = "unknown"
            if s.get("closest_name"):
                # The score matters even when nothing matched — it is how you
                # tell "threshold slightly too high" from "never seen before".
                tag += f"  (closest {s['closest_name']} {s.get('similarity', 0):.3f})"
            unknown += 1
        print(
            f"  {s['speaker_label']:<12} {mins:6.1f} min  "
            f"{s['share'] * 100:5.1f}%  {s['segment_count']:>4} seg   {tag}"
        )
    print()
    if unknown:
        print(f"{unknown} cluster(s) unmatched — name them with:")
        print(f"  skipcast label '{segments_path}'")
    print(f"Open it:  open '{preview_path}'")
    return 0


def _resolve_audio(doc: dict, segments_path: Path) -> Path:
    """Find the audio a segments document refers to, tolerating a moved folder."""
    for candidate in (
        Path(doc.get("audio_path") or ""),
        segments_path.parent / doc["audio_file"],
    ):
        if candidate and candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"cannot find {doc['audio_file']} — expected it next to {segments_path}"
    )


def _cmd_label(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import labeler

    cfg = load_config(args.config)
    segments_path = Path(args.segments).expanduser().resolve()
    if not segments_path.is_file():
        print(f"error: no such file: {segments_path}", file=sys.stderr)
        return 1

    doc = json.loads(segments_path.read_text())
    if not any(s.get("embedding") for s in doc["speakers"]):
        print(
            "error: this segments file has no embeddings — it predates Phase 1.\n"
            "       re-run: skipcast analyze -f <audio>",
            file=sys.stderr,
        )
        return 1

    try:
        audio_path = _resolve_audio(doc, segments_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    labeler.serve(doc, cfg, audio_path, open_browser=not args.no_browser)
    return 0


def _report(ok: bool, name: str, done: str) -> None:
    if ok:
        print(done)
    else:
        print(f"unknown speaker: {name}", file=sys.stderr)


def _cmd_speakers(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db

    cfg = load_config(args.config)
    conn = db.connect(cfg)

    # With --feed, skip/unskip write an override for that show only; without
    # it they set the global flag, which is what they have always done.
    feed_id = None
    if args.feed:
        feed = db.get_feed(conn, args.feed)
        if feed is None:
            print(f"error: no feed with slug '{args.feed}'", file=sys.stderr)
            conn.close()
            return 1
        feed_id = feed["id"]
    if args.clear and not args.feed:
        print("error: --clear removes a per-feed override, so it needs --feed",
              file=sys.stderr)
        conn.close()
        return 1

    def apply(name: str, skip: bool | None) -> bool:
        if feed_id is None:
            return db.set_skip(conn, name, bool(skip))
        return db.set_feed_rule(conn, name, feed_id, skip)

    where = f" on {args.feed}" if args.feed else ""
    changed = False
    for name in args.skip or []:
        _report(apply(name, True), name, f"skip: {name}{where}")
        changed = True
    for name in args.unskip or []:
        _report(apply(name, False), name, f"keep: {name}{where}")
        changed = True
    for name in args.clear or []:
        _report(apply(name, None), name,
                f"cleared: {name}{where}, now follows the global flag")
        changed = True
    for name in args.forget or []:
        if db.forget(conn, name):
            print(f"forgotten: {name}")
        else:
            print(f"unknown speaker: {name}", file=sys.stderr)
        changed = True
    if changed:
        print()

    speakers = db.list_speakers(conn)
    if not speakers:
        print("No speakers known yet. Run: skipcast label <segments.json>")
        conn.close()
        return 0

    print(f"{'skip':<6}{'name':<24}{'samples':>8}{'talk time':>12}")
    for s in speakers:
        mins = s.total_seconds / 60
        print(f"{'  X   ' if s.skip else '      '}{s.name:<24}"
              f"{s.profile_count:>8}{mins:>10.1f}m")

    rules = db.rules_by_feed(conn)
    if rules:
        print("\nper-feed overrides (these win over the global flag):")
        for r in rules:
            verb = "cut from" if r["skip"] else "kept on"
            print(f"  {r['speaker']:<24} {verb} {r['slug']}")
    conn.close()
    return 0


def _cmd_person(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, person

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    base = cfg.serve.base_url.rstrip("/")

    if args.action == "add":
        if not args.name:
            print("error: person add needs a speaker name", file=sys.stderr)
            conn.close()
            return 1
        try:
            made = person.create(conn, cfg, args.name, args.slug,
                                 args.min_minutes * 60)
        except person.PersonError as exc:
            print(f"error: {exc}", file=sys.stderr)
            conn.close()
            return 1
        print(f"person feed: {made['slug']}")
        print(f"  speaker:  {made['speaker']} ({made['profiles']} voice sample(s))")
        print(f"  minimum:  {made['min_seconds'] / 60:.0f} min of talk per episode")
        print(f"  feed url: {base}/persons/{made['slug']}.xml")
        print(f"\nNext: skipcast person build --slug {made['slug']}")
        conn.close()
        return 0

    if args.action == "remove":
        if not args.slug:
            print("error: person remove needs --slug", file=sys.stderr)
            conn.close()
            return 1
        ok = db.remove_person_feed(conn, args.slug)
        print("removed" if ok else f"no person feed with slug '{args.slug}'")
        print("derived audio was left on disk" if ok else "")
        conn.close()
        return 0 if ok else 1

    if args.action == "build":
        rows = db.list_person_feeds(conn)
        if args.slug:
            rows = [r for r in rows if r["slug"] == args.slug]
            if not rows:
                print(f"error: no person feed with slug '{args.slug}'", file=sys.stderr)
                conn.close()
                return 1
        if not rows:
            print('No person feeds. Add one with: skipcast person add "<name>"')
            conn.close()
            return 0
        for pf in rows:
            report = person.build(conn, cfg, pf, limit=args.limit, force=args.force)
            print()
            print(f"{pf['slug']}: {report.count('ready')} episode(s) ready, "
                  f"{report.count('skipped')} without them, "
                  f"{report.count('failed')} failed")
            for a in report.appearances:
                if a.status == "ready":
                    print(f"  {a.seconds / 60:5.1f} min  {a.episode_title[:52]}"
                          f"  ({a.feed_slug})")
            print(f"  feed url: {base}/persons/{pf['slug']}.xml")
        conn.close()
        return 0

    rows = db.list_person_feeds(conn)
    if not rows:
        print('No person feeds. Add one with: skipcast person add "<name>"')
        conn.close()
        return 0
    for r in rows:
        print(r["slug"])
        print(f"  speaker: {r['speaker']}")
        print(f"  ready:   {r['ready_count']} appearance(s), "
              f"{(r['total_seconds'] or 0) / 60:.0f} min total")
        print(f"  minimum: {r['min_seconds'] / 60:.0f} min per episode")
        print(f"  built:   {r['built_at'] or 'never'}")
        print(f"  serve:   {base}/persons/{r['slug']}.xml")
    conn.close()
    return 0


def _cmd_enroll(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, enroll

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    try:
        got = enroll.enroll_clip(conn, cfg, args.name, Path(args.audio),
                                 start=args.start, end=args.end)
    except (enroll.EnrollError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        conn.close()
        return 1

    print()
    print(f"enrolled: {got.name}")
    print(f"  from:   {got.source}")
    print(f"  speech: {got.seconds:.0f}s")
    if got.matched_existing and got.matched_existing != got.name:
        # Not an error — but a voice that already scores high against someone
        # else is either the same person under two names or a threshold worth
        # looking at, and both are better known now than after ten episodes.
        print(f"\nnote: this voice scores {got.similarity_to_existing:.2f} against "
              f"'{got.matched_existing}', who is already known.")
        print("      If they are the same person, drop one with: "
              f"skipcast speakers --forget \"{got.name}\"")
    print(f"\n{got.name} will now be recognised in episodes polled from here on.")
    print("Already-processed episodes need: skipcast poll --force, or a re-cut.")
    conn.close()
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, search, timeline

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    try:
        hits = search.search(conn, args.query, limit=args.limit,
                             feed_slug=args.feed, speaker=args.speaker)
    except search.SearchUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        conn.close()
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        conn.close()
        return 1

    if not hits:
        indexed = search.stats(conn)
        if not indexed["episodes"]:
            print("Nothing is indexed yet. Run: skipcast index")
        else:
            print(f"No matches in {indexed['episodes']} indexed episode(s).")
        conn.close()
        return 0

    def hms(t: float) -> str:
        return f"{int(t) // 3600:d}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}" \
            if t >= 3600 else f"{int(t) // 60:d}:{int(t) % 60:02d}"

    timelines: dict[str, timeline.Timeline] = {}
    for h in hits:
        if h.episode_key not in timelines:
            ep = db.get_episode_by_key(conn, h.episode_key)
            timelines[h.episode_key] = (
                timeline.for_episode(ep) if ep else timeline.identity_timeline()
            )
        tl = timelines[h.episode_key]
        # Both clocks: the original is where it was said, the edit is where to
        # seek to in what actually gets served.
        cut_at = f"  edit {hms(tl.to_cut(h.start))}"
        if tl.was_cut(h.start):
            cut_at = "  [removed from your copy]"
        print(f"{hms(h.start)}{cut_at}")
        print(f"  {h.speaker} — {h.episode_title[:60]} ({h.feed_slug})")
        text = h.snippet.replace(search.MARK_OPEN, "\033[1m") \
                        .replace(search.MARK_CLOSE, "\033[0m") \
            if sys.stdout.isatty() else \
            h.snippet.replace(search.MARK_OPEN, "").replace(search.MARK_CLOSE, "")
        print(f"  {text}")
        print()
    print(f"{len(hits)} hit(s)")
    conn.close()
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, poll as poller, search

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    try:
        search.ensure_schema(conn)
    except search.SearchUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        conn.close()
        return 1
    poller.reindex_transcripts(conn, cfg, only_missing=args.missing)
    search.prune(conn)

    from . import entities

    entities.reindex_all(conn, log=lambda m: print(m, file=sys.stderr))

    stats = search.stats(conn)
    ent = entities.stats(conn)
    print(f"\n{stats['passages']} passages from {stats['episodes']} episode(s) "
          "are searchable")
    print(f"{ent['mentions']} specifics from {ent['episodes']} summarised "
          "episode(s) are indexed")
    print('Try: skipcast search "<something said>"  or  skipcast entities NVDA')
    conn.close()
    return 0


def _cmd_entities(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, entities

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    found = entities.lookup(conn, args.term or "", args.type or "", args.limit,
                            evidence=args.evidence or "")
    if not found:
        have = entities.stats(conn)
        if not have["mentions"]:
            print("Nothing indexed yet. Summarise an episode, then: skipcast index")
        else:
            print(f"No match in {have['mentions']} indexed specifics.")
            kinds = ", ".join(f"{k} ({n})" for k, n in entities.types(conn)[:8])
            print(f"Types available: {kinds}")
        conn.close()
        return 0

    for m in found:
        stamp = ""
        if m.at_seconds is not None:
            stamp = f"  [{int(m.at_seconds) // 60}:{int(m.at_seconds) % 60:02d}]"
        flag = f"  ({m.confidence})" if m.confidence and m.confidence != "firm" else ""
        # The evidence grade is what was offered in the episode, not a verdict
        # on the claim, so it reads as "backed by" rather than "true".
        if m.evidence:
            flag += f"  [{m.evidence}]"
        print(f"{m.value}  <{m.type}>{flag}")
        if m.detail:
            print(f"  {m.detail}")
        who = f" — {m.speaker}" if m.speaker else ""
        print(f"  {m.episode_title[:60]} ({m.feed_slug}){stamp}{who}")
        print()
    print(f"{len(found)} mention(s)")
    conn.close()
    return 0


def _cmd_digest(args: argparse.Namespace) -> int:
    from .audio import FFmpegFailed, FFmpegMissing
    from .config import load_config
    from . import cut as cutter, db, digest

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    base = cfg.serve.base_url.rstrip("/")

    if args.action == "list":
        rows = digest.recent(conn)
        if not rows:
            print("No digests yet. Build one with: skipcast digest build --minutes 30")
            conn.close()
            return 0
        for r in rows:
            pieces = json.loads(r["pieces"] or "[]")
            print(f"{r['key']}  {(r['seconds'] or 0) / 60:.0f} min  "
                  f"{len(pieces)} topic(s)  {r['created_at'][:10]}")
            print(f"  {r['title']}")
            print(f"  play: {base}/digests/{r['key']}.mp3")
        conn.close()
        return 0

    if args.action == "remove":
        ok = digest.remove(conn, args.key or "")
        print("removed (audio left on disk)" if ok else "no such digest")
        conn.close()
        return 0 if ok else 1

    try:
        made = digest.build(conn, cfg, args.minutes, feed_slug=args.feed,
                            unplayed_only=not args.include_played)
    except digest.DigestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        conn.close()
        return 1
    except (cutter.CutRefused, FFmpegFailed, FFmpegMissing) as exc:
        print(f"error: {exc}", file=sys.stderr)
        conn.close()
        return 1

    print()
    print(f"digest: {made['title']}")
    print(f"  length: {made['seconds'] / 60:.1f} min")
    for p in made["pieces"]:
        print(f"  {p['seconds'] / 60:5.1f} min  {p['topic'][:52]}  ({p['feed_slug']})")
    print(f"\n  play: {base}/digests/{made['key']}.mp3")
    print(f"  feed: {base}/digests.xml")
    conn.close()
    return 0


def _cmd_overlaps(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, overlap

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    clusters = overlap.across_library(conn, days=args.days, limit=args.limit)
    if not clusters:
        have = conn.execute("SELECT COUNT(*) n FROM topics").fetchone()["n"]
        if not have:
            print("No topics indexed yet. Summarise some episodes, then: "
                  "skipcast index")
        else:
            print(f"No repeated stories among {have} topics in the last "
                  f"{args.days} days.")
        conn.close()
        return 0

    for k in clusters:
        print(f"{k['count']} shows covered this:")
        for s in k["shows"]:
            stamp = ""
            if s["start_seconds"] is not None:
                stamp = (f"  [{int(s['start_seconds']) // 60}:"
                         f"{int(s['start_seconds']) % 60:02d}]")
            print(f"  {s['feed_slug']:<20} {s['topic'][:56]}{stamp}")
        if k["shared"]:
            print(f"  in common: {', '.join(k['shared'][:6])}")
        print()
    print(f"{len(clusters)} repeated stor{'y' if len(clusters) == 1 else 'ies'}")
    conn.close()
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, entities

    cfg = load_config(args.config)
    conn = db.connect(cfg)

    if args.action == "add":
        if not args.term:
            print("error: watch add needs a term", file=sys.stderr)
            conn.close()
            return 1
        entities.watch_add(conn, args.term)
        print(f"watching: {args.term}")
        conn.close()
        return 0

    if args.action == "remove":
        ok = entities.watch_remove(conn, args.term or "")
        print("removed" if ok else f"not watching '{args.term}'")
        conn.close()
        return 0 if ok else 1

    hits = entities.watch_hits(conn)
    if not hits:
        print('Nothing on the watchlist. Add one with: skipcast watch add "NVDA"')
        conn.close()
        return 0

    for h in hits:
        flag = f"  {h['new']} new" if h["new"] else ""
        print(f"{h['term']}  —  {h['total']} mention(s){flag}")
        for m in h["mentions"][:args.limit]:
            stamp = ""
            if m.at_seconds is not None:
                stamp = f" [{int(m.at_seconds) // 60}:{int(m.at_seconds) % 60:02d}]"
            print(f"  {m.value}: {m.detail[:80]}")
            print(f"    {m.episode_title[:55]} ({m.feed_slug}){stamp}")
        print()
    if args.seen:
        n = entities.watch_mark_seen(conn)
        print(f"marked {n} term(s) as seen")
    conn.close()
    return 0


def _cmd_cut(args: argparse.Namespace) -> int:
    from .audio import FFmpegFailed, FFmpegMissing
    from .config import load_config
    from . import cut as cutter

    cfg = load_config(args.config)
    segments_path = Path(args.segments).expanduser().resolve()
    if not segments_path.is_file():
        print(f"error: no such file: {segments_path}", file=sys.stderr)
        return 1
    doc = json.loads(segments_path.read_text())

    src = Path(args.audio).expanduser().resolve()
    if not src.is_file():
        print(f"error: no such file: {src}", file=sys.stderr)
        return 1

    try:
        plan = cutter.build_plan(doc, cfg, args.speaker)
    except cutter.CutRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    if not plan.skipped_labels:
        print(
            "No speakers are flagged skip in this episode, so there is nothing to "
            "cut.\nMark someone with:  skipcast speakers --skip \"<name>\"\n"
            "or override for this run with:  --speaker SPEAKER_06",
            file=sys.stderr,
        )
        return 1

    named = []
    for label in plan.skipped_labels:
        spk = next(s for s in doc["speakers"] if s["speaker_label"] == label)
        named.append(f"{spk.get('matched_name') or label} ({label})")
    print(f"cutting: {', '.join(named)}")
    print(cutter.describe(plan))

    stem = src.name.rsplit(".", 1)[0]
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else src.parent
    dest = Path(args.output).expanduser().resolve() if args.output \
        else out_dir / f"{stem}.cut.mp3"
    log_path = out_dir / f"{stem}.cuts.json"

    out_dir.mkdir(parents=True, exist_ok=True)
    cutter.write_log(plan, doc, cfg, log_path)

    if args.dry_run:
        print(f"\ndry run — nothing encoded\nlog: {log_path}")
        return 0

    print("\nencoding…")
    try:
        cutter.render(src, plan, cfg, dest)
    except (cutter.CutRefused, FFmpegFailed, FFmpegMissing) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    from . import audio as _audio

    actual = _audio.duration_seconds(dest)
    print(f"\noutput: {dest}")
    print(f"log:    {log_path}")
    print(f"length: {actual / 60:.1f} min (was {plan.duration / 60:.1f} min)")
    # Crossfades overlap the joins, so the file is shorter than the sum of the
    # kept pieces by roughly crossfade x joins. Flag anything beyond that.
    expected = plan.result_seconds - cfg.cut.crossfade_seconds * max(0, len(plan.keeps) - 1)
    if abs(actual - expected) > 2.0:
        print(
            f"warning: expected about {expected / 60:.1f} min, got {actual / 60:.1f}. "
            "Check the cut log.",
            file=sys.stderr,
        )
    return 0


def _cmd_subscribe(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, feeds

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    try:
        meta, entries = feeds.parse(args.url)
    except feeds.FeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    slug = args.slug or feeds.slugify(meta.get("title") or args.url)
    existing = db.get_feed(conn, slug)
    if existing and existing["url"] != args.url:
        print(f"error: slug '{slug}' is already used by {existing['url']}\n"
              "       pass --slug to choose another", file=sys.stderr)
        return 1

    # Re-subscribing with --slug renames an existing feed rather than failing.
    already = conn.execute(
        "SELECT id, slug FROM feeds WHERE url = ?", (args.url,)
    ).fetchone()
    if already and args.slug and already["slug"] != slug:
        conn.execute("UPDATE feeds SET slug = ? WHERE id = ?", (slug, already["id"]))
        conn.commit()
        print(f"renamed feed {already['slug']} -> {slug}")

    db.add_feed(conn, slug, args.url, meta)
    print(f"subscribed: {meta.get('title') or args.url}")
    print(f"  slug:     {slug}")
    print(f"  episodes: {len(entries)} in feed")
    print(f"  feed url: {cfg.serve.base_url.rstrip('/')}/feeds/{slug}.xml")
    print(f"\nNext: skipcast poll --feed {slug}")
    conn.close()
    return 0


def _cmd_feeds(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    rows = db.list_feeds(conn)
    if not rows:
        print("No feeds. Run: skipcast subscribe <feed-url>")
        conn.close()
        return 0
    base = cfg.serve.base_url.rstrip("/")
    for r in rows:
        print(f"{r['slug']}")
        print(f"  title:  {r['title'] or '(unknown)'}")
        print(f"  source: {r['url']}")
        print(f"  serve:  {base}/feeds/{r['slug']}.xml")
        print(f"  ready:  {r['ready_count']}/{r['total_count']} episodes")
        print(f"  polled: {r['last_polled_at'] or 'never'}")
    conn.close()
    return 0


def _cmd_poll(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db, poll as poller

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    rows = db.list_feeds(conn)
    if args.feed:
        rows = [r for r in rows if r["slug"] == args.feed]
        if not rows:
            print(f"error: no feed with slug '{args.feed}'", file=sys.stderr)
            conn.close()
            return 1
    if not rows:
        print("No feeds. Run: skipcast subscribe <feed-url>")
        conn.close()
        return 0

    totals = {"ready": 0, "skipped": 0, "failed": 0, "refused": 0}
    for row in rows:
        report = poller.poll_feed(conn, cfg, row, limit=args.limit, force=args.force)
        for o in report.outcomes:
            totals[o.status] = totals.get(o.status, 0) + 1

    print()
    print(f"processed {totals['ready']} new, skipped {totals['skipped']} already done, "
          f"{totals['failed']} failed, {totals['refused']} refused")
    if totals["failed"] or totals["refused"]:
        print("see: skipcast episodes --problems")
    conn.close()
    return 0


def _cmd_episodes(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db

    cfg = load_config(args.config)
    conn = db.connect(cfg)
    sql = "SELECT e.*, f.slug FROM episodes e JOIN feeds f ON f.id = e.feed_id"
    if args.problems:
        sql += " WHERE e.status IN ('failed','refused')"
    sql += " ORDER BY e.published_ts DESC LIMIT ?"
    rows = conn.execute(sql, (args.limit,)).fetchall()
    if not rows:
        print("No episodes." if not args.problems else "No failed or refused episodes.")
        conn.close()
        return 0
    for r in rows:
        cut = f"{(r['cut_seconds'] or 0) / 60:.0f}m cut" if r["status"] == "ready" else ""
        print(f"[{r['status']:<8}] {r['slug']}  {(r['title'] or '')[:58]}  {cut}")
        if r["error"]:
            print(f"           {r['error'][:150]}")
    conn.close()
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import server

    cfg = load_config(args.config)
    if args.port:
        cfg.serve.port = args.port
    if args.base_url:
        cfg.serve.base_url = args.base_url
    server.run(cfg)
    return 0


def _cmd_service(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import service

    cfg = load_config(args.config)

    if args.action == "install":
        base = cfg.serve.base_url.rstrip("/")
        try:
            path = service.install(cfg)
        except (RuntimeError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"installed: {path}")
        print(f"logs:      {cfg.data_dir / 'logs'}/skipcast.log")
        print()
        print("The server now starts at login and restarts if it dies.")
        if cfg.poll.interval_hours > 0:
            print(f"Feeds are polled every {cfg.poll.interval_hours:g}h; "
                  "scheduled polls show up in the control panel's Activity tab.")
        else:
            print("Automatic polling is off ([poll] interval_hours = 0).")
        if "localhost" in base or "127.0.0.1" in base:
            print("\nwarning: base_url is still localhost, so your phone cannot "
                  "use the feed.\n         Set [serve] base_url, then re-run "
                  "'skipcast service install'.")
        return 0

    if args.action == "uninstall":
        print("removed" if service.uninstall() else "was not installed")
        return 0

    st = service.status()
    print(f"plist:     {st['plist']}")
    print(f"installed: {'yes' if st['installed'] else 'no'}")
    print(f"loaded:    {'yes' if st['loaded'] else 'no'}")
    if st["pid"]:
        print(f"pid:       {st['pid']}")
    if st["state"]:
        print(f"state:     {st['state']}")
    if not st["installed"]:
        print("\nInstall with: skipcast service install")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skipcast",
        description="Remove specific speakers from podcast episodes.",
    )
    parser.add_argument("--version", action="version", version=f"skipcast {__version__}")
    parser.add_argument(
        "--config", type=Path, default=None,
        help="path to config.toml (default: ./config.toml, then the project root)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser(
        "analyze",
        help="diarize an audio file or URL, writing segments JSON + a preview page",
    )
    analyze.add_argument(
        "audio",
        help="path to an audio file, or a URL (YouTube and anything else yt-dlp handles)",
    )
    analyze.add_argument(
        "-o", "--out-dir", default=None,
        help="where to write outputs (default: alongside the audio file)",
    )
    analyze.add_argument(
        "-f", "--force", action="store_true",
        help="re-run diarization even if a segments JSON already exists",
    )
    analyze.add_argument(
        "--refetch", action="store_true",
        help="for a URL, download again instead of reusing the local copy",
    )
    analyze.set_defaults(func=_cmd_analyze)

    fetch_p = sub.add_parser(
        "fetch",
        help="download audio from a URL without diarizing it",
    )
    fetch_p.add_argument("url", help="a URL yt-dlp can handle")
    fetch_p.add_argument(
        "-f", "--force", action="store_true",
        help="download again even if we already have this one",
    )
    fetch_p.set_defaults(func=_cmd_fetch)

    label = sub.add_parser(
        "label",
        help="name the diarized clusters in a segments file and store voice profiles",
    )
    label.add_argument("segments", help="path to a .segments.json")
    label.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening it"
    )
    label.set_defaults(func=_cmd_label)

    speakers = sub.add_parser(
        "speakers", help="list known speakers and toggle their skip flag"
    )
    speakers.add_argument("--skip", action="append", metavar="NAME",
                          help="mark a speaker to be cut (repeatable)")
    speakers.add_argument("--unskip", action="append", metavar="NAME",
                          help="stop cutting a speaker (repeatable)")
    speakers.add_argument("--feed", default=None, metavar="SLUG",
                          help="scope --skip/--unskip to one podcast instead of "
                               "every feed")
    speakers.add_argument("--clear", action="append", metavar="NAME",
                          help="drop a speaker's override on --feed, so the "
                               "global flag applies again")
    speakers.add_argument("--forget", action="append", metavar="NAME",
                          help="delete a speaker and all their voice profiles")
    speakers.set_defaults(func=_cmd_speakers)

    person_p = sub.add_parser(
        "person", help="feeds built from one voice across every subscribed show"
    )
    person_p.add_argument("action", choices=["list", "add", "build", "remove"],
                          nargs="?", default="list")
    person_p.add_argument("name", nargs="?", default=None,
                          help='for add: the speaker, e.g. "Chamath Palihapatiya"')
    person_p.add_argument("--slug", default=None,
                          help="short name used in the served feed URL")
    person_p.add_argument("--min-minutes", type=float, default=2.0,
                          help="ignore appearances shorter than this (default 2)")
    person_p.add_argument("--limit", type=int, default=None,
                          help="for build: only the newest N episodes")
    person_p.add_argument("--force", action="store_true",
                          help="for build: redo appearances already built")
    person_p.set_defaults(func=_cmd_person)

    enroll_p = sub.add_parser(
        "enroll", help="learn a voice from a sample clip, without an episode"
    )
    enroll_p.add_argument("name", help='who this is, e.g. "Sam Altman"')
    enroll_p.add_argument("audio", help="an audio file of them speaking")
    enroll_p.add_argument("--start", type=float, default=0.0,
                          help="seconds into the file to start the sample")
    enroll_p.add_argument("--end", type=float, default=None,
                          help="seconds into the file to end the sample")
    enroll_p.set_defaults(func=_cmd_enroll)

    search_p = sub.add_parser(
        "search", help="full-text search across every transcript"
    )
    search_p.add_argument("query",
                          help='words to find; quote a phrase, trail a * for prefix')
    search_p.add_argument("--feed", default=None, help="only this feed slug")
    search_p.add_argument("--speaker", default=None,
                          help="only passages attributed to this speaker")
    search_p.add_argument("--limit", type=int, default=20)
    search_p.set_defaults(func=_cmd_search)

    index_p = sub.add_parser(
        "index",
        help="build the search and specifics indexes from what is on disk",
    )
    index_p.add_argument("--missing", action="store_true",
                         help="only episodes not indexed yet, rather than all")
    index_p.set_defaults(func=_cmd_index)

    ent_p = sub.add_parser(
        "entities",
        help="tickers, dates, figures and claims, across every summary",
    )
    ent_p.add_argument("term", nargs="?", default=None,
                       help="what to look for; omit to list everything")
    ent_p.add_argument("--type", default=None,
                       help="only this kind, e.g. ticker, figure, claim")
    ent_p.add_argument("--evidence", default=None,
                       help="only claims backed this way: trial, observational, "
                            "mechanism, anecdote, authority, none — or 'any' for "
                            "everything that was graded")
    ent_p.add_argument("--limit", type=int, default=30)
    ent_p.set_defaults(func=_cmd_entities)

    dig_p = sub.add_parser(
        "digest", help="one file made of the best topics, to fit the time you have"
    )
    dig_p.add_argument("action", choices=["build", "list", "remove"],
                       nargs="?", default="build")
    dig_p.add_argument("--minutes", type=float, default=30.0,
                       help="how long you have (default 30)")
    dig_p.add_argument("--feed", default=None, help="only from this feed slug")
    dig_p.add_argument("--include-played", action="store_true",
                       help="consider episodes you have already finished")
    dig_p.add_argument("--key", default=None, help="for remove")
    dig_p.set_defaults(func=_cmd_digest)

    ovl_p = sub.add_parser(
        "overlaps", help="stories that more than one subscribed show covered"
    )
    ovl_p.add_argument("--days", type=int, default=21,
                       help="how far apart two episodes can be and still count")
    ovl_p.add_argument("--limit", type=int, default=20)
    ovl_p.set_defaults(func=_cmd_overlaps)

    watch_p = sub.add_parser(
        "watch", help="terms to be told about when a summary mentions them"
    )
    watch_p.add_argument("action", choices=["list", "add", "remove"],
                         nargs="?", default="list")
    watch_p.add_argument("term", nargs="?", default=None)
    watch_p.add_argument("--limit", type=int, default=5,
                         help="mentions shown per term")
    watch_p.add_argument("--seen", action="store_true",
                         help="mark everything shown as seen")
    watch_p.set_defaults(func=_cmd_watch)

    cut_p = sub.add_parser(
        "cut", help="produce an edited audio file with the skipped speakers removed"
    )
    cut_p.add_argument("audio", help="the source audio file")
    cut_p.add_argument("segments", help="its .segments.json")
    cut_p.add_argument("-o", "--output", default=None,
                       help="output file (default: <name>.cut.mp3 beside the source)")
    cut_p.add_argument("--out-dir", default=None,
                       help="where to write output and the cut log")
    cut_p.add_argument("--speaker", action="append", metavar="NAME_OR_LABEL",
                       help="cut this speaker regardless of the stored skip flag "
                            "(repeatable)")
    cut_p.add_argument("-n", "--dry-run", action="store_true",
                       help="write the cut log and report, but encode nothing")
    cut_p.set_defaults(func=_cmd_cut)

    sub_p = sub.add_parser("subscribe", help="add a podcast feed")
    sub_p.add_argument("url", help="the podcast's RSS feed URL")
    sub_p.add_argument("--slug", default=None,
                       help="short name used in the served feed URL")
    sub_p.set_defaults(func=_cmd_subscribe)

    feeds_p = sub.add_parser("feeds", help="list subscribed feeds")
    feeds_p.set_defaults(func=_cmd_feeds)

    poll_p = sub.add_parser(
        "poll", help="fetch new episodes and run the pipeline over them"
    )
    poll_p.add_argument("--feed", default=None, help="only this feed slug")
    poll_p.add_argument("--limit", type=int, default=None,
                        help="episodes to consider per feed (default: poll.max_episodes)")
    poll_p.add_argument("--force", action="store_true",
                        help="reprocess even episodes already marked ready")
    poll_p.set_defaults(func=_cmd_poll)

    eps_p = sub.add_parser("episodes", help="list processed episodes")
    eps_p.add_argument("--problems", action="store_true",
                       help="only failed and refused episodes")
    eps_p.add_argument("--limit", type=int, default=30)
    eps_p.set_defaults(func=_cmd_episodes)

    serve_p = sub.add_parser("serve", help="serve the generated feeds and audio")
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.add_argument("--base-url", default=None,
                         help="public base URL used in enclosure links")
    serve_p.set_defaults(func=_cmd_serve)

    svc = sub.add_parser(
        "service", help="run the server automatically via launchd (macOS)"
    )
    svc.add_argument("action", choices=["install", "uninstall", "status"],
                     nargs="?", default="status")
    svc.set_defaults(func=_cmd_service)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
