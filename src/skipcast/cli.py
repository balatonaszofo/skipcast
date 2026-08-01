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


def _cmd_speakers(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import db

    cfg = load_config(args.config)
    conn = db.connect(cfg)

    changed = False
    for name in args.skip or []:
        if db.set_skip(conn, name, True):
            print(f"skip: {name}")
        else:
            print(f"unknown speaker: {name}", file=sys.stderr)
        changed = True
    for name in args.unskip or []:
        if db.set_skip(conn, name, False):
            print(f"keep: {name}")
        else:
            print(f"unknown speaker: {name}", file=sys.stderr)
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
    speakers.add_argument("--forget", action="append", metavar="NAME",
                          help="delete a speaker and all their voice profiles")
    speakers.set_defaults(func=_cmd_speakers)

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
