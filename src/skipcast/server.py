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
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from . import db, feeds, identity, jobs
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
        state = {
            "base_url": cfg.serve.base_url.rstrip("/"),
            "search_enabled": cfg.serve.enable_search,
            "threshold": cfg.identity.match_threshold,
            "feeds": [_row(r) for r in db.list_feeds(c)],
            "speakers": [
                {"name": s.name, "skip": s.skip, "profiles": s.profile_count,
                 "total_seconds": s.total_seconds}
                for s in db.list_speakers(c)
            ],
            "jobs": [_row(r) for r in jobs.recent(c, 12)],
        }
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
            identity.annotate(doc, identity.match_document(doc, c, cfg))
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
        c.close()
        return out

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
        identity.annotate(doc, identity.match_document(doc, c, cfg))
        Path(row["segments_path"]).write_text(json.dumps(doc, indent=2),
                                              encoding="utf-8")
        c.close()
        return {"ok": True}

    @app.post("/api/episodes/{key}/{action}")
    async def api_episode_job(key: str, action: str):
        _need_ui()
        if action not in ("recut", "reprocess"):
            raise HTTPException(404, "unknown action")
        c = conn()
        row = db.get_episode_by_key(c, key)
        if row is None:
            c.close()
            raise HTTPException(404, "unknown episode")
        label = ("Recut" if action == "recut" else "Reprocess")
        jid = jobs.enqueue(c, action, key, f"{label} {(row['title'] or '')[:40]}")
        c.close()
        worker.poke()
        return {"job_id": jid}

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
