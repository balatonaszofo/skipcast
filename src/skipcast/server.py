"""FastAPI app: the generated feeds, our own audio, and the control panel.

Single machine, single user, reached over Tailscale — so no auth, no
multi-tenancy, per the project's constraints. The control panel exists so the
phone can do everything except the processing itself: subscribe, poll, name
speakers, choose who gets cut. The desktop stays the box that does the work.

Range support is not optional: podcast clients seek, resume part-played
episodes, and download in chunks.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from . import db, feeds, identity, jobs, timeline
from .config import Config

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 262144


def _ranged(path: Path, request: Request, media_type: str) -> Response:
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    headers = {"accept-ranges": "bytes", "content-type": media_type}

    rng = request.headers.get("range")
    if rng:
        m = _RANGE_RE.match(rng.strip())
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = int(m.group(2))
            if start >= size:
                return Response(status_code=416,
                                headers={"content-range": f"bytes */{size}"})
            end = min(end, size - 1)
            status = 206
            headers["content-range"] = f"bytes {start}-{end}/{size}"

    length = end - start + 1
    headers["content-length"] = str(length)

    def stream():
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(stream(), status_code=status, headers=headers)


def _row(r) -> dict:
    return dict(r) if r is not None else {}


def _stamp(items: list[dict], tl: timeline.Timeline) -> list[dict]:
    """Add edited-file positions to anything carrying an original timestamp.

    Summaries and transcripts are timed against the source audio; the player
    plays the cut. Sending both means the UI can offer a jump link that lands
    where the listener expects, and say so when a moment was removed outright.
    """
    for item in items:
        at = item.get("at_seconds")
        if at is None:
            item["at_cut"] = None
            item["removed"] = False
        else:
            item["at_cut"] = round(tl.to_cut(float(at)), 2)
            item["removed"] = tl.was_cut(float(at))
    return items


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="skipcast", docs_url=None, redoc_url=None)
    worker = jobs.worker_for(cfg) if cfg.serve.enable_ui else None

    def conn():
        # A connection per request: SQLite is cheap to open, and this sidesteps
        # sharing one across the server's worker threads.
        c = db.connect(cfg, check_same_thread=False)
        jobs.ensure_schema(c)
        return c

    def _need_ui():
        if not cfg.serve.enable_ui:
            raise HTTPException(404, "control panel disabled in config")

    # ---- pages ------------------------------------------------------------
    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def index():
        from .webui import PAGE

        if not cfg.serve.enable_ui:
            c = conn()
            rows = db.list_feeds(c)
            c.close()
            base = cfg.serve.base_url.rstrip("/")
            items = "".join(
                f'<li><code>{base}/feeds/{r["slug"]}.xml</code> — {r["title"] or ""}</li>'
                for r in rows
            ) or "<li>No feeds yet.</li>"
            return f"<html><body><h1>skipcast</h1><ul>{items}</ul></body></html>"
        return PAGE

    # ---- feed + audio (what the podcast app talks to) ---------------------
    @app.api_route("/feeds/{slug}.xml", methods=["GET", "HEAD"])
    def feed(slug: str):
        c = conn()
        row = db.get_feed(c, slug)
        if row is None:
            c.close()
            raise HTTPException(404, f"no feed named {slug}")
        episodes = db.feed_episodes(c, row["id"], ready_only=True)
        xml = feeds.render_feed(row, episodes, cfg.serve.base_url)
        c.close()
        return Response(content=xml,
                        media_type="application/rss+xml; charset=utf-8",
                        headers={"cache-control": "no-cache"})

    @app.api_route("/digests.xml", methods=["GET", "HEAD"])
    def digest_feed():
        from . import digest as digester

        c = conn()
        xml = feeds.render_digest_feed(digester.recent(c, 50), cfg.serve.base_url)
        c.close()
        return Response(content=xml,
                        media_type="application/rss+xml; charset=utf-8",
                        headers={"cache-control": "no-cache"})

    @app.api_route("/digests/{key}.mp3", methods=["GET", "HEAD"])
    def digest_audio(key: str, request: Request):
        from . import digest as digester

        c = conn()
        row = digester.get(c, key)
        c.close()
        if row is None or not row["audio_path"]:
            raise HTTPException(404, "unknown digest")
        path = Path(row["audio_path"])
        if not path.is_file():
            raise HTTPException(410, "digest audio no longer on disk; rebuild it")
        return _ranged(path, request, "audio/mpeg")

    @app.api_route("/persons/{slug}.xml", methods=["GET", "HEAD"])
    def person_feed(slug: str):
        """One voice, every show they appear on."""
        c = conn()
        row = db.get_person_feed(c, slug)
        if row is None:
            c.close()
            raise HTTPException(404, f"no person feed named {slug}")
        episodes = db.person_episodes(c, row["id"], ready_only=True)
        xml = feeds.render_person_feed(row, episodes, cfg.serve.base_url)
        c.close()
        return Response(content=xml,
                        media_type="application/rss+xml; charset=utf-8",
                        headers={"cache-control": "no-cache"})

    @app.api_route("/persons/audio/{key}.mp3", methods=["GET", "HEAD"])
    def person_audio(key: str, request: Request):
        c = conn()
        row = db.get_person_episode(c, key)
        c.close()
        if row is None or not row["audio_path"]:
            raise HTTPException(404, "unknown appearance")
        path = Path(row["audio_path"])
        if not path.is_file():
            raise HTTPException(410, "audio no longer on disk; rebuild this feed")
        return _ranged(path, request, "audio/mpeg")

    @app.api_route("/audio/{key}.mp3", methods=["GET", "HEAD"])
    def audio(key: str, request: Request):
        c = conn()
        row = db.get_episode_by_key(c, key)
        c.close()
        if row is None or not row["cut_path"]:
            raise HTTPException(404, "unknown episode")
        path = Path(row["cut_path"])
        if not path.is_file():
            raise HTTPException(410, "audio no longer on disk; reprocess this episode")
        return _ranged(path, request, "audio/mpeg")

    @app.api_route("/highlights/{key}.mp3", methods=["GET", "HEAD"])
    def highlight_audio(key: str, request: Request):
        """A saved clip, cut on first request and kept afterwards.

        Rendering here rather than at capture time means the clips that are
        never played never cost anything, and a clip that has been retrimmed
        rebuilds itself the next time somebody asks for it.
        """
        from . import highlights as hl

        c = conn()
        try:
            path = hl.render(c, cfg, key)
        except hl.HighlightError as exc:
            message = str(exc)
            raise HTTPException(404 if "no highlight" in message else 410,
                                message) from exc
        finally:
            c.close()
        return _ranged(path, request, "audio/mpeg")

    @app.api_route("/source/{key}.mp3", methods=["GET", "HEAD"])
    def source_audio(key: str, request: Request):
        """The uncut download — what speaker samples are played from."""
        c = conn()
        row = db.get_episode_by_key(c, key)
        c.close()
        if row is None or not row["source_path"]:
            raise HTTPException(404, "unknown episode")
        path = Path(row["source_path"])
        if not path.is_file():
            raise HTTPException(410, "source audio no longer on disk")
        return _ranged(path, request, "audio/mpeg")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ---- control panel API -----------------------------------------------
    @app.get("/api/state")
    def api_state():
        _need_ui()
        c = conn()
        from . import summarize as summarizer

        state = {
            "base_url": cfg.serve.base_url.rstrip("/"),
            "search_enabled": cfg.serve.enable_search,
            "threshold": cfg.identity.match_threshold,
            # Whether summarising can actually run, so the UI can say why not
            # instead of queueing a job that fails on the API key.
            "summary_enabled": cfg.summary.enabled,
            "summary_provider": cfg.summary.provider,
            "summary_ready": cfg.summary.enabled and summarizer.available(cfg),
            "highlights_enabled": cfg.highlight.enabled,
            "highlight_lookback": cfg.highlight.lookback_seconds,
            "feeds": [_row(r) for r in db.list_feeds(c)],
            "speakers": [
                {"name": s.name, "skip": s.skip, "profiles": s.profile_count,
                 "total_seconds": s.total_seconds}
                for s in db.list_speakers(c)
            ],
            "feed_rules": db.rules_by_feed(c),
            "jobs": [_row(r) for r in jobs.recent(c, 12)],
        }
        try:
            from . import search

            state["search"] = search.stats(c)
        except Exception as exc:  # noqa: BLE001 — no FTS5 is not a broken panel
            state["search"] = {"passages": 0, "episodes": 0, "error": str(exc)}
        c.close()
        return state

    @app.get("/api/episodes")
    def api_all_episodes(limit: int = 100):
        """Everything playable, for the Listen tab."""
        _need_ui()
        c = conn()
        pos = db.positions(c)
        out = []
        for r in db.ready_episodes(c, limit):
            d = _row(r)
            p = pos.get(r["key"], {})
            d["position"] = p.get("position", 0.0)
            d["finished"] = p.get("finished", False)
            out.append(d)
        c.close()
        return {"episodes": out}

    @app.post("/api/playback/{key}")
    async def api_playback(key: str, request: Request):
        _need_ui()
        body = await request.json()
        c = conn()
        db.set_position(c, key, float(body.get("position") or 0),
                        body.get("duration"), bool(body.get("finished")))
        c.close()
        return {"ok": True}

    @app.api_route("/manifest.webmanifest", methods=["GET", "HEAD"])
    def manifest():
        # Lets the control panel install to the home screen and open without
        # browser chrome, which is what makes it feel like a podcast app.
        return JSONResponse({
            "name": "skipcast",
            "short_name": "skipcast",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#0f1115",
            "theme_color": "#0f1115",
            "icons": [
                {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
                 "purpose": "any maskable"},
            ],
        }, media_type="application/manifest+json")

    @app.api_route("/icon.svg", methods=["GET", "HEAD"])
    def icon():
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
            '<rect width="512" height="512" rx="96" fill="#0f1115"/>'
            '<circle cx="256" cy="256" r="150" fill="none" stroke="#4f9cf9"'
            ' stroke-width="28"/>'
            '<path d="M212 186l132 70-132 70z" fill="#4f9cf9"/>'
            '</svg>'
        )
        return Response(content=svg, media_type="image/svg+xml",
                        headers={"cache-control": "max-age=86400"})

    @app.get("/api/search")
    def api_search(q: str):
        _need_ui()
        if not cfg.serve.enable_search:
            raise HTTPException(403, "search disabled in config")
        from . import discovery

        try:
            return {"results": discovery.search(q)}
        except discovery.SearchError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/transcripts/search")
    def api_transcript_search(q: str, limit: int = 40, feed: str | None = None,
                              speaker: str | None = None):
        """Search everything ever transcribed.

        Hits come back with two timestamps: where it was said in the original,
        and where that lands in the edited audio the player serves.
        """
        _need_ui()
        from . import search

        c = conn()
        try:
            hits = search.search(c, q, limit=min(limit, 200), feed_slug=feed,
                                 speaker=speaker)
        except search.SearchUnavailable as exc:
            c.close()
            raise HTTPException(501, str(exc)) from exc
        except ValueError as exc:
            c.close()
            raise HTTPException(400, str(exc)) from exc

        # One timeline per episode, not per hit: a query matching forty
        # passages in one episode would otherwise re-read the same cut log
        # forty times.
        timelines: dict[str, timeline.Timeline] = {}
        out = []
        for h in hits:
            if h.episode_key not in timelines:
                ep = db.get_episode_by_key(c, h.episode_key)
                timelines[h.episode_key] = (
                    timeline.for_episode(ep) if ep else timeline.identity_timeline()
                )
            tl = timelines[h.episode_key]
            out.append({
                "episode_key": h.episode_key,
                "episode_title": h.episode_title,
                "feed_slug": h.feed_slug,
                "feed_title": h.feed_title,
                "speaker": h.speaker,
                "start": round(h.start, 2),
                "at_cut": round(tl.to_cut(h.start), 2),
                "removed": tl.was_cut(h.start),
                "snippet_html": search.to_html(h.snippet),
                "score": round(h.score, 3),
            })
        c.close()
        return {"query": q, "count": len(out), "results": out}

    @app.post("/api/feeds")
    async def api_subscribe(request: Request):
        _need_ui()
        body = await request.json()
        url = (body.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url required")
        try:
            meta, entries = feeds.parse(url)
        except feeds.FeedError as exc:
            raise HTTPException(400, str(exc)) from exc

        wanted = (body.get("slug") or "").strip()
        slug = wanted or feeds.slugify(meta.get("title") or url)
        c = conn()

        # Already subscribed to this URL? Keep the existing row. Renaming it is
        # only correct when a slug was asked for explicitly — otherwise the
        # caller gets back a slug that does not exist.
        existing = c.execute("SELECT id, slug FROM feeds WHERE url = ?",
                             (url,)).fetchone()
        if existing:
            if wanted and wanted != existing["slug"]:
                c.execute("UPDATE feeds SET slug = ? WHERE id = ?",
                          (slug, existing["id"]))
                c.commit()
            else:
                slug = existing["slug"]
        elif db.get_feed(c, slug) is not None:
            # Different feed already holds this slug.
            slug = f"{slug}-{abs(hash(url)) % 1000}"

        fid = db.add_feed(c, slug, url, meta)
        c.close()
        return {"slug": slug, "title": meta.get("title"), "episodes": len(entries),
                "feed_id": fid, "already_subscribed": bool(existing)}

    @app.delete("/api/feeds/{slug}")
    def api_unsubscribe(slug: str):
        _need_ui()
        c = conn()
        row = db.get_feed(c, slug)
        if row is None:
            c.close()
            raise HTTPException(404, "no such feed")
        # Episodes cascade. Audio files are left on disk deliberately —
        # deleting a subscription should not silently destroy downloads.
        c.execute("DELETE FROM feeds WHERE id = ?", (row["id"],))
        c.commit()
        # The search index sits outside that foreign key, so it has to be told
        # separately or the results keep pointing at episodes that are gone.
        try:
            from . import search

            search.prune(c)
        except Exception:  # noqa: BLE001 — unsubscribing must still succeed
            pass
        c.close()
        return {"ok": True, "note": "downloaded files were left on disk"}

    @app.get("/api/feeds/{slug}/episodes")
    def api_feed_episodes(slug: str):
        _need_ui()
        c = conn()
        row = db.get_feed(c, slug)
        if row is None:
            c.close()
            raise HTTPException(404, "no such feed")
        eps = [_row(r) for r in db.feed_episodes(c, row["id"], ready_only=False)]
        c.close()
        return {"feed": _row(row), "episodes": eps}

    @app.post("/api/feeds/{slug}/poll")
    async def api_poll(slug: str, request: Request):
        _need_ui()
        body = await request.json() if await request.body() else {}
        c = conn()
        row = db.get_feed(c, slug)
        if row is None:
            c.close()
            raise HTTPException(404, "no such feed")
        jid = jobs.enqueue(c, "poll", slug, f"Poll {slug}", {
            "limit": body.get("limit"), "force": bool(body.get("force")),
        })
        c.close()
        worker.poke()
        return {"job_id": jid}

    @app.get("/api/episodes/{key}")
    def api_episode(key: str):
        _need_ui()
        c = conn()
        row = db.get_episode_by_key(c, key)
        if row is None:
            c.close()
            raise HTTPException(404, "unknown episode")
        out = _row(row)
        out["clusters"] = []
        seg = row["segments_path"]
        if seg and Path(seg).is_file():
            from .labeler import pick_samples
            from .preview import PALETTE

            doc = json.loads(Path(seg).read_text())
            # Re-match on read rather than trusting what was stored. Naming a
            # voice should light up every episode that voice appears in, not
            # only the ones processed since.
            identity.annotate(doc, identity.match_document(
                doc, c, cfg, feed_id=row["feed_id"]))
            samples = pick_samples(doc)
            for i, s in enumerate(doc["speakers"]):
                out["clusters"].append({
                    "speaker_label": s["speaker_label"],
                    "color": PALETTE[i % len(PALETTE)],
                    "total_seconds": s["total_seconds"],
                    "segment_count": s["segment_count"],
                    "share": s["share"],
                    "matched_name": s.get("matched_name"),
                    "closest_name": s.get("closest_name"),
                    "similarity": s.get("similarity", 0.0),
                    "skip": bool(s.get("skip")),
                    "has_embedding": bool(s.get("embedding")),
                    "samples": samples.get(s["speaker_label"], []),
                })
        cuts = row["cuts_path"]
        if cuts and Path(cuts).is_file():
            out["cut_log"] = json.loads(Path(cuts).read_text())["summary"]

        summary = row["summary_path"]
        out["summary"] = (
            Path(summary).read_text() if summary and Path(summary).is_file() else None
        )

        # The structured half, with every timestamp also expressed in the
        # edited audio's clock so the UI can jump straight to it.
        out["index"] = None
        idx = row["summary_json_path"]
        if idx and Path(idx).is_file():
            try:
                data = json.loads(Path(idx).read_text())
                tl = timeline.for_episode(row)
                data["topics"] = _stamp(data.get("topics") or [], tl)
                data["specifics"] = _stamp(data.get("specifics") or [], tl)
                # Which chapters are gone because of a skipped subject, so the
                # page can show them struck through with the term responsible
                # rather than silently omitting them.
                from . import entities

                by_pos = {
                    ch["position"]: ch
                    for ch in entities.skipped_chapters(c, row["id"], row["feed_id"])
                }
                for i, t in enumerate(data["topics"]):
                    ch = by_pos.get(i)
                    t["skipped_by"] = ch["terms"] if ch else []
                    if ch:
                        t["at_seconds"] = ch["start_seconds"]
                out["index"] = data
            except ValueError:
                pass

        # Where else this week's topics turned up. Attached per topic position
        # so the UI can say it next to the topic rather than as a footnote.
        out["related"] = {}
        if out["index"]:
            from . import overlap

            try:
                out["related"] = {
                    str(pos): [
                        {"episode_key": r.episode_key, "feed_title": r.feed_title,
                         "feed_slug": r.feed_slug, "topic": r.topic_title,
                         "score": r.score, "shared": r.shared,
                         "published_ts": r.published_ts}
                        for r in rel
                    ]
                    for pos, rel in overlap.for_episode(c, key).items()
                }
            except Exception:  # noqa: BLE001 — an annotation, not the page
                pass

        transcript = row["transcript_path"]
        out["has_transcript"] = bool(transcript and Path(transcript).is_file())
        c.close()
        return out

    @app.get("/api/episodes/{key}/transcript")
    def api_transcript(key: str):
        _need_ui()
        c = conn()
        row = db.get_episode_by_key(c, key)
        c.close()
        path = Path(row["transcript_path"]) if row and row["transcript_path"] else None
        if path is None or not path.is_file():
            raise HTTPException(404, "no transcript for this episode")
        from . import transcribe as stt

        return Response(content=stt.as_text(json.loads(path.read_text())),
                        media_type="text/plain; charset=utf-8")

    @app.post("/api/episodes/{key}/label")
    async def api_label(key: str, request: Request):
        _need_ui()
        body = await request.json()
        cluster = body.get("cluster_label")
        name = (body.get("name") or "").strip()
        c = conn()
        row = db.get_episode_by_key(c, key)
        if row is None or not row["segments_path"]:
            c.close()
            raise HTTPException(404, "unknown episode")
        doc = json.loads(Path(row["segments_path"]).read_text())
        try:
            if not name:
                raise ValueError("name required")
            identity.store_profile(c, doc, cluster, name,
                                   Path(row["source_path"] or key).name)
        except ValueError as exc:
            c.close()
            raise HTTPException(400, str(exc)) from exc
        # Reflect the new identity in the stored document immediately.
        identity.annotate(doc, identity.match_document(
            doc, c, cfg, feed_id=row["feed_id"]))
        Path(row["segments_path"]).write_text(json.dumps(doc, indent=2),
                                              encoding="utf-8")
        c.close()
        return {"ok": True}

    @app.post("/api/episodes/{key}/{action}")
    async def api_episode_job(key: str, action: str):
        _need_ui()
        if action not in ("recut", "reprocess", "summarize"):
            raise HTTPException(404, "unknown action")
        c = conn()
        row = db.get_episode_by_key(c, key)
        if row is None:
            c.close()
            raise HTTPException(404, "unknown episode")
        label = {"recut": "Recut", "reprocess": "Reprocess",
                 "summarize": "Summarise"}[action]
        jid = jobs.enqueue(c, action, key, f"{label} {(row['title'] or '')[:40]}")
        c.close()
        worker.poke()
        return {"job_id": jid}

    @app.post("/api/feeds/{slug}/rules")
    async def api_set_feed_rule(slug: str, request: Request):
        """Cut or keep one speaker on this feed only.

        skip = true / false sets an override; skip = null clears it and hands
        the decision back to the speaker's global flag.
        """
        _need_ui()
        body = await request.json()
        name = (body.get("name") or "").strip()
        raw = body.get("skip", None)
        skip = None if raw is None else bool(raw)
        c = conn()
        feed = db.get_feed(c, slug)
        if feed is None:
            c.close()
            raise HTTPException(404, "no such feed")
        ok = db.set_feed_rule(c, name, feed["id"], skip)
        c.close()
        if not ok:
            raise HTTPException(404, "unknown speaker")
        return {"ok": True, "note": "existing episodes need a re-cut"}

    @app.post("/api/speakers/{name}/skip")
    async def api_set_skip(name: str, request: Request):
        _need_ui()
        body = await request.json()
        c = conn()
        ok = db.set_skip(c, name, bool(body.get("skip")))
        c.close()
        if not ok:
            raise HTTPException(404, "unknown speaker")
        return {"ok": True}

    @app.delete("/api/speakers/{name}")
    def api_forget(name: str):
        _need_ui()
        c = conn()
        ok = db.forget(c, name)
        c.close()
        if not ok:
            raise HTTPException(404, "unknown speaker")
        return {"ok": True}

    def _mention_dicts(c, mentions) -> list[dict]:
        """Mentions with a position in the edit, one timeline per episode."""
        timelines: dict[str, timeline.Timeline] = {}
        out = []
        for m in mentions:
            if m.episode_key not in timelines:
                ep = db.get_episode_by_key(c, m.episode_key)
                timelines[m.episode_key] = (
                    timeline.for_episode(ep) if ep else timeline.identity_timeline()
                )
            tl = timelines[m.episode_key]
            d = {
                "value": m.value, "type": m.type, "detail": m.detail,
                "speaker": m.speaker, "confidence": m.confidence,
                "at_seconds": m.at_seconds, "episode_key": m.episode_key,
                "episode_title": m.episode_title, "feed_slug": m.feed_slug,
                "feed_title": m.feed_title,
            }
            if m.at_seconds is None:
                d["at_cut"], d["removed"] = None, False
            else:
                d["at_cut"] = round(tl.to_cut(float(m.at_seconds)), 2)
                d["removed"] = tl.was_cut(float(m.at_seconds))
            out.append(d)
        return out

    @app.get("/api/entities")
    def api_entities(q: str = "", type: str = "", limit: int = 50):
        """Tickers, dates, figures and claims, across every summary."""
        _need_ui()
        from . import entities

        c = conn()
        found = entities.lookup(c, q, type, min(limit, 200))
        out = {
            "count": len(found),
            "types": [{"type": t, "count": n} for t, n in entities.types(c)],
            "results": _mention_dicts(c, found),
        }
        c.close()
        return out

    @app.get("/api/digests")
    def api_digests():
        _need_ui()
        from . import digest as digester

        c = conn()
        out = []
        for r in digester.recent(c, 30):
            d = _row(r)
            try:
                d["pieces"] = json.loads(r["pieces"] or "[]")
            except ValueError:
                d["pieces"] = []
            out.append(d)
        c.close()
        return {"digests": out}

    @app.post("/api/digests")
    async def api_build_digest(request: Request):
        _need_ui()
        try:
            body = await request.json() if await request.body() else {}
        except ValueError as exc:
            raise HTTPException(422, "request body must be valid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(422, "request body must be a JSON object")
        try:
            raw_minutes = body.get("minutes")
            if isinstance(raw_minutes, bool):
                raise ValueError
            minutes = float(30 if raw_minutes is None else raw_minutes)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "minutes must be a number") from exc
        if not math.isfinite(minutes) or not 1 <= minutes <= 120:
            raise HTTPException(422, "minutes must be between 1 and 120")
        c = conn()
        active = c.execute(
            "SELECT id FROM jobs WHERE kind='digest' AND status IN ('queued','running') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if active is not None:
            c.close()
            raise HTTPException(409, "a brief is already being prepared")
        jid = jobs.enqueue(c, "digest", None,
                           f"Digest {minutes:g} min",
                           {"minutes": minutes,
                            "feed": body.get("feed") or None,
                            "include_played": bool(body.get("include_played"))})
        c.close()
        worker.poke()
        return {"job_id": jid}

    @app.delete("/api/digests/{key}")
    def api_remove_digest(key: str):
        _need_ui()
        from . import digest as digester

        c = conn()
        ok = digester.remove(c, key)
        c.close()
        if not ok:
            raise HTTPException(404, "no such digest")
        return {"ok": True, "note": "audio was left on disk"}

    # ---- highlights -------------------------------------------------------
    def _need_highlights():
        _need_ui()
        if not cfg.highlight.enabled:
            raise HTTPException(404, "highlights disabled in config")

    async def _json_body(request: Request) -> dict:
        try:
            body = await request.json() if await request.body() else {}
        except ValueError as exc:
            raise HTTPException(422, "request body must be valid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(422, "request body must be a JSON object")
        return body

    def _number(body: dict, name: str, default=None):
        raw = body.get(name)
        if raw is None:
            return default
        if isinstance(raw, bool):
            raise HTTPException(422, f"{name} must be a number")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"{name} must be a number") from exc
        if not math.isfinite(value):
            raise HTTPException(422, f"{name} must be a finite number")
        return value

    @app.get("/api/highlights")
    def api_highlights(episode: str | None = None, limit: int = 100):
        _need_highlights()
        from . import highlights as hl

        c = conn()
        out = [hl.as_dict(r) for r in hl.recent(c, max(1, min(limit, 500)), episode)]
        c.close()
        return {"highlights": out}

    @app.post("/api/highlights")
    async def api_capture_highlight(request: Request):
        """Save the stretch of audio just played.

        The position is where the *player* is, which is a position in the
        edited file; converting it to the original clock is capture's job, not
        the browser's.
        """
        _need_highlights()
        from . import highlights as hl

        body = await _json_body(request)
        key = body.get("episode_key") or body.get("key")
        if not isinstance(key, str) or not key:
            raise HTTPException(422, "episode_key is required")
        position = _number(body, "position")
        if position is None:
            raise HTTPException(422, "position is required")
        lookback = _number(body, "lookback")
        note = body.get("note")

        c = conn()
        try:
            saved = hl.capture(c, cfg, key, position, lookback,
                               note if isinstance(note, str) else None)
        except hl.HighlightError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            c.close()
        return saved

    @app.get("/api/highlights/{key}/segments")
    def api_highlight_segments(key: str):
        """The clip's sentences, each with its own timing — the trim targets."""
        _need_highlights()
        from . import highlights as hl

        c = conn()
        try:
            return {"segments": hl.segments(c, key)}
        except hl.HighlightError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            c.close()

    @app.post("/api/highlights/{key}/trim")
    async def api_trim_highlight(key: str, request: Request):
        _need_highlights()
        from . import highlights as hl

        body = await _json_body(request)
        start, end = _number(body, "start"), _number(body, "end")
        if start is None or end is None:
            raise HTTPException(422, "start and end are required")
        c = conn()
        try:
            return hl.retrim(c, cfg, key, start, end)
        except hl.HighlightError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            c.close()

    @app.post("/api/highlights/{key}/note")
    async def api_note_highlight(key: str, request: Request):
        _need_highlights()
        from . import highlights as hl

        body = await _json_body(request)
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            raise HTTPException(422, "note must be a string")
        c = conn()
        row = hl.get(c, key)
        if row is None:
            c.close()
            raise HTTPException(404, "no such highlight")
        c.execute("UPDATE highlights SET note = ?, updated_at = ? WHERE id = ?",
                  ((note or "").strip() or None, hl._now(), row["id"]))
        c.commit()
        out = hl.as_dict(hl.get(c, key))
        c.close()
        return out

    @app.delete("/api/highlights/{key}")
    def api_remove_highlight(key: str):
        _need_highlights()
        from . import highlights as hl

        c = conn()
        ok = hl.remove(c, key)
        c.close()
        if not ok:
            raise HTTPException(404, "no such highlight")
        return {"ok": True}

    @app.get("/api/overlaps")
    def api_overlaps(days: int = 21, limit: int = 40):
        """Stories that more than one show covered."""
        _need_ui()
        from . import overlap

        c = conn()
        out = overlap.across_library(c, days=days, limit=min(limit, 100))
        c.close()
        return {"days": days, "count": len(out), "clusters": out}

    @app.get("/api/watchlist")
    def api_watchlist(limit: int = 8):
        _need_ui()
        from . import entities

        c = conn()
        out = []
        for h in entities.watch_hits(c, limit):
            out.append({"term": h["term"], "new": h["new"], "total": h["total"],
                        "mentions": _mention_dicts(c, h["mentions"])})
        c.close()
        return {"watchlist": out}

    @app.post("/api/watchlist")
    async def api_watch_add(request: Request):
        _need_ui()
        from . import entities

        body = await request.json()
        c = conn()
        try:
            entities.watch_add(c, (body.get("term") or "").strip())
        except ValueError as exc:
            c.close()
            raise HTTPException(400, str(exc)) from exc
        c.close()
        return {"ok": True}

    @app.delete("/api/watchlist/{term}")
    def api_watch_remove(term: str):
        _need_ui()
        from . import entities

        c = conn()
        ok = entities.watch_remove(c, term)
        c.close()
        if not ok:
            raise HTTPException(404, "not watching that")
        return {"ok": True}

    @app.post("/api/watchlist/seen")
    def api_watch_seen():
        _need_ui()
        from . import entities

        c = conn()
        n = entities.watch_mark_seen(c)
        c.close()
        return {"ok": True, "terms": n}

    @app.get("/api/topics")
    def api_topics():
        """Every term with an opinion attached, and what it has actually done."""
        _need_ui()
        from . import entities

        c = conn()
        cut_counts: dict[str, dict] = {}
        for r in c.execute(
            "SELECT cut_topics, topic_seconds FROM episodes "
            "WHERE cut_topics IS NOT NULL AND cut_topics != ''"
        ):
            names = [n.strip() for n in (r["cut_topics"] or "").split(",") if n.strip()]
            for n in names:
                acc = cut_counts.setdefault(n.casefold(),
                                            {"episodes": 0, "seconds": 0.0})
                acc["episodes"] += 1
                # topic_seconds is the episode's total across its terms; split
                # it evenly rather than overstating each one.
                acc["seconds"] += float(r["topic_seconds"] or 0) / len(names)
        hits = {h["term"]: h for h in entities.watch_hits(c, 3)}
        out = []
        for row in entities.terms(c):
            state = entities.state_of(row)
            done = cut_counts.get(row["term_norm"], {})
            h = hits.get(row["term"])
            out.append({
                "term": row["term"], "state": state,
                "episodes": done.get("episodes", 0),
                "seconds": round(done.get("seconds", 0.0)),
                "new": h["new"] if h else 0,
                "mentions": h["total"] if h else 0,
            })
        rules = entities.feed_rules(c)
        c.close()
        return {"topics": out, "rules": rules}

    @app.post("/api/topics")
    async def api_topic_state(request: Request):
        """Set a term's state, and re-cut only if that changed what is skipped.

        Watching a term, or adding one fresh, touches no audio — only crossing
        into or out of skip does, so only that is worth an ffmpeg pass over
        every affected episode. The recuts are queued rather than run here:
        each one takes minutes, and the caller is a tap on a phone.
        """
        _need_ui()
        from . import entities

        body = await request.json()
        term = (body.get("term") or "").strip()
        state = body.get("state")
        if state not in ("watch", "skip", None):
            raise HTTPException(400, "state must be watch, skip or null")
        c = conn()
        was_skip = any(
            r["term_norm"] == entities.normalize(term)
            and entities.state_of(r) == "skip"
            for r in entities.terms(c)
        )
        try:
            entities.set_state(c, term, state)
        except ValueError as exc:
            c.close()
            raise HTTPException(400, str(exc)) from exc
        now_skip = state == "skip"
        queued = (_requeue_for_term(c, term)
                  if body.get("recut", True) and was_skip != now_skip else 0)
        c.close()
        worker.poke()
        return {"ok": True, "recutting": queued}

    def _requeue_for_term(c, term: str) -> int:
        """Re-cut every ready episode whose chapters mention a term.

        Both directions need this: starting a skip removes chapters, and
        stopping one has to put them back.
        """
        from . import entities

        keys = {h["episode_key"] for h in entities.skip_impact(c, term)}
        # Episodes already carrying the term in their cut log need rebuilding
        # too — that is how a skip gets undone.
        for r in c.execute("SELECT key, cut_topics FROM episodes "
                           "WHERE cut_topics IS NOT NULL AND cut_topics != ''"):
            names = {n.strip().casefold()
                     for n in (r["cut_topics"] or "").split(",")}
            if entities.normalize(term) in names:
                keys.add(r["key"])
        for key in keys:
            ep = db.get_episode_by_key(c, key)
            if ep is not None and ep["status"] == "ready":
                jobs.enqueue(c, "recut", key, f"Re-cut {ep['title'][:40]}", {})
        return len(keys)

    @app.get("/api/topics/impact")
    def api_topic_impact(term: str):
        """What skipping this term would remove, before anything is cut."""
        _need_ui()
        from . import entities

        c = conn()
        hits = entities.skip_impact(c, term)
        c.close()
        return {
            "term": term, "count": len(hits),
            "seconds": round(sum(h["seconds"] for h in hits)),
            "episodes": len({h["episode_key"] for h in hits}),
            "chapters": hits[:20],
        }

    @app.post("/api/feeds/{slug}/topic-rules")
    async def api_topic_rule(slug: str, request: Request):
        _need_ui()
        from . import entities

        body = await request.json()
        c = conn()
        row = db.get_feed(c, slug)
        if row is None:
            c.close()
            raise HTTPException(404, "no such feed")
        entities.set_feed_rule(c, (body.get("term") or "").strip(),
                               row["id"], body.get("skip"))
        queued = _requeue_for_term(c, (body.get("term") or "").strip())
        c.close()
        worker.poke()
        return {"ok": True, "recutting": queued}

    @app.get("/api/feeds/{slug}/segments")
    def api_feed_segments(slug: str):
        """Segments this show runs most weeks, as skip suggestions."""
        _need_ui()
        from . import entities

        c = conn()
        row = db.get_feed(c, slug)
        if row is None:
            c.close()
            raise HTTPException(404, "no such feed")
        known = {entities.normalize(t["term"]) for t in entities.terms(c)}
        out = [s for s in entities.recurring_segments(c, row["id"])
               if entities.normalize(s["term"]) not in known]
        c.close()
        return {"segments": out}

    @app.post("/api/episodes/{key}/hide")
    async def api_hide_episode(key: str, request: Request):
        """Drop an episode from Listen, or put it back. Files stay on disk."""
        _need_ui()
        body = await request.json() if await request.body() else {}
        hidden = bool(body.get("hidden", True))
        c = conn()
        row = db.get_episode_by_key(c, key)
        if row is None:
            c.close()
            raise HTTPException(404, "unknown episode")
        db.upsert_episode(c, row["feed_id"], key, row["guid"],
                          {"hidden": 1 if hidden else 0,
                           "skip_note": None if hidden else row["skip_note"]})
        c.close()
        return {"ok": True, "hidden": hidden}

    @app.post("/api/episodes/{key}/keep")
    def api_keep_episode(key: str):
        """Listen anyway: clear the mostly-skipped note and leave it whole."""
        _need_ui()
        c = conn()
        row = db.get_episode_by_key(c, key)
        if row is None:
            c.close()
            raise HTTPException(404, "unknown episode")
        db.upsert_episode(c, row["feed_id"], key, row["guid"],
                          {"skip_note": None})
        c.close()
        return {"ok": True}

    @app.get("/api/persons")
    def api_persons():
        _need_ui()
        c = conn()
        out = []
        for r in db.list_person_feeds(c):
            d = _row(r)
            d["episodes"] = [
                {"key": e["key"], "title": e["title"], "feed_title": e["feed_title"],
                 "seconds": e["seconds"], "talk_seconds": e["talk_seconds"],
                 "published": e["published"]}
                for e in db.person_episodes(c, r["id"], ready_only=True)
            ]
            out.append(d)
        c.close()
        return {"persons": out}

    @app.post("/api/persons")
    async def api_add_person(request: Request):
        _need_ui()
        from . import person

        body = await request.json()
        c = conn()
        try:
            made = person.create(c, cfg, (body.get("name") or "").strip(),
                                 (body.get("slug") or "").strip() or None,
                                 float(body.get("min_minutes") or 2) * 60)
        except person.PersonError as exc:
            c.close()
            raise HTTPException(400, str(exc)) from exc
        c.close()
        return made

    @app.delete("/api/persons/{slug}")
    def api_remove_person(slug: str):
        _need_ui()
        c = conn()
        ok = db.remove_person_feed(c, slug)
        c.close()
        if not ok:
            raise HTTPException(404, "no such person feed")
        return {"ok": True, "note": "derived audio was left on disk"}

    @app.post("/api/persons/{slug}/build")
    async def api_build_person(slug: str, request: Request):
        _need_ui()
        body = await request.json() if await request.body() else {}
        c = conn()
        row = db.get_person_feed(c, slug)
        if row is None:
            c.close()
            raise HTTPException(404, "no such person feed")
        jid = jobs.enqueue(c, "person", slug, f"Build {slug}",
                           {"force": bool(body.get("force"))})
        c.close()
        worker.poke()
        return {"job_id": jid}

    @app.post("/api/reindex")
    def api_reindex():
        """Rebuild the search index from the transcripts already on disk."""
        _need_ui()
        c = conn()
        jid = jobs.enqueue(c, "reindex", None, "Rebuild search index")
        c.close()
        worker.poke()
        return {"job_id": jid}

    @app.get("/api/jobs")
    def api_jobs(limit: int = 15):
        _need_ui()
        c = conn()
        out = [_row(r) for r in jobs.recent(c, limit)]
        c.close()
        return {"jobs": out}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: int):
        _need_ui()
        c = conn()
        row = jobs.get(c, job_id)
        c.close()
        if row is None:
            raise HTTPException(404, "unknown job")
        return _row(row)

    @app.post("/api/jobs/{job_id}/cancel")
    def api_cancel(job_id: int):
        _need_ui()
        c = conn()
        ok = jobs.cancel(c, job_id)
        c.close()
        return JSONResponse({"ok": ok, "note": "" if ok else
                             "only queued jobs can be cancelled"})

    return app


def run(cfg: Config) -> None:
    import uvicorn

    base = cfg.serve.base_url.rstrip("/")
    print(f"serving on http://{cfg.serve.host}:{cfg.serve.port}")
    if cfg.serve.enable_ui:
        print(f"control panel: {base}/")
    print(f"feeds advertised as {base}/feeds/<slug>.xml")
    if "localhost" in base or "127.0.0.1" in base:
        print(
            "\nwarning: base_url still points at localhost, so the enclosure URLs in\n"
            "         the generated feed will not resolve from your phone. Set\n"
            "         [serve] base_url in config.toml to this machine's tailnet name."
        )
    uvicorn.run(create_app(cfg), host=cfg.serve.host, port=cfg.serve.port,
                log_level="warning")
