"""`skipcast label` — a small local web UI for naming diarized clusters.

Deliberately stdlib http.server rather than FastAPI: Phase 3 is where a real
server earns its dependency. This one binds to 127.0.0.1, serves one page,
and exits when you close it.

Range requests are implemented because the page seeks into a 90 MB MP3 to play
samples; without Range the browser has to download the whole file before it can
play a segment two-thirds of the way in.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import db, identity
from .config import Config
from .preview import PALETTE

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _free_port(preferred: int = 8731) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


class _Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, ctx=None, **kwargs):
        self.ctx = ctx
        super().__init__(*args, **kwargs)

    def log_message(self, *args):  # keep the console clean
        pass

    # ---- helpers ----------------------------------------------------------
    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_audio(self, head_only: bool = False):
        path: Path = self.ctx["audio_path"]
        if not path.is_file():
            self.send_error(404, "audio file not found")
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        rng = self.headers.get("Range")
        if rng:
            m = _RANGE_RE.match(rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only:
            return
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return  # browser seeked away mid-stream; normal
                remaining -= len(chunk)

    # ---- routes -----------------------------------------------------------
    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/":
            self._html(render_page(self.ctx))
        elif route == "/audio":
            self._serve_audio()
        elif route == "/api/state":
            self._json(build_state(self.ctx))
        else:
            self.send_error(404)

    def do_HEAD(self):
        # Media elements HEAD the URL to learn size and Range support before
        # they will seek into it.
        if self.path.split("?")[0] == "/audio":
            self._serve_audio(head_only=True)
        else:
            self.send_error(404)

    def do_POST(self):
        route = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")

        if route == "/api/label":
            try:
                self._json(apply_label(self.ctx, payload))
            except ValueError as exc:
                self._json({"error": str(exc)}, status=400)
        elif route == "/api/skip":
            with self.ctx["lock"]:
                ok = db.set_skip(self.ctx["conn"], payload["name"], bool(payload["skip"]))
            self._json({"ok": ok, "state": build_state(self.ctx)})
        elif route == "/api/done":
            self._json({"ok": True})
            threading.Thread(target=self.ctx["shutdown"], daemon=True).start()
        else:
            self.send_error(404)


# ---- state -----------------------------------------------------------------
def build_state(ctx) -> dict:
    doc, conn, cfg = ctx["doc"], ctx["conn"], ctx["cfg"]
    with ctx["lock"]:
        matches = {m.cluster_label: m for m in identity.match_document(doc, conn, cfg)}
        known = {s.name: s for s in db.list_speakers(conn)}
    assigned = ctx["assigned"]

    clusters = []
    for i, spk in enumerate(doc["speakers"]):
        label = spk["speaker_label"]
        m = matches.get(label)
        name = assigned.get(label)
        clusters.append({
            "cluster_label": label,
            "color": PALETTE[i % len(PALETTE)],
            "total_seconds": spk["total_seconds"],
            "share": spk["share"],
            "segment_count": spk["segment_count"],
            "has_embedding": bool(spk.get("embedding")),
            "samples": ctx["samples"].get(label, []),
            "assigned": name,
            "auto_name": m.name if m else None,
            "closest_name": m.closest_name if m else None,
            "similarity": round(m.similarity, 3) if m else 0.0,
            "runner_up": m.runner_up if m else None,
            "runner_up_similarity": round(m.runner_up_similarity, 3) if m else 0.0,
        })

    return {
        "title": doc.get("title") or doc["audio_file"],
        "source": ctx["source"],
        "threshold": cfg.identity.match_threshold,
        "clusters": clusters,
        "speakers": [
            {"name": s.name, "skip": s.skip, "profiles": s.profile_count}
            for s in known.values()
        ],
    }


def apply_label(ctx, payload: dict) -> dict:
    label = payload.get("cluster_label")
    name = (payload.get("name") or "").strip()
    doc, conn = ctx["doc"], ctx["conn"]

    spk = next((s for s in doc["speakers"] if s["speaker_label"] == label), None)
    if spk is None:
        raise ValueError(f"unknown cluster {label}")

    if name in ("", identity.IGNORE):
        ctx["assigned"][label] = identity.IGNORE if name == identity.IGNORE else None
        return {"ok": True, "state": build_state(ctx)}

    if not spk.get("embedding"):
        raise ValueError(
            f"{label} has no usable embedding, so it cannot be stored as a profile"
        )

    with ctx["lock"]:
        speaker_id = db.get_or_create_speaker(conn, name)
        db.add_profile(
            conn, speaker_id, ctx["source"], label, spk["embedding"],
            spk["total_seconds"],
        )
    ctx["assigned"][label] = name
    return {"ok": True, "state": build_state(ctx)}


def pick_samples(doc: dict, per_cluster: int = 3) -> dict[str, list[dict]]:
    """The longest few turns per cluster — the clearest sample of a voice."""
    by_label: dict[str, list[dict]] = {}
    for seg in doc["segments"]:
        by_label.setdefault(seg["speaker_label"], []).append(seg)
    out = {}
    for label, segs in by_label.items():
        best = sorted(segs, key=lambda s: s["end"] - s["start"], reverse=True)
        out[label] = [
            {"start": s["start"], "end": s["end"],
             "duration": round(s["end"] - s["start"], 1)}
            for s in best[:per_cluster]
        ]
    return out


# ---- server ----------------------------------------------------------------
def serve(doc: dict, cfg: Config, audio_path: Path, open_browser: bool = True) -> None:
    # Request handlers run on worker threads, so the connection has to allow
    # cross-thread use and every touch of it goes through ctx["lock"].
    conn = db.connect(cfg, check_same_thread=False)
    port = _free_port()
    ctx = {
        "doc": doc,
        "cfg": cfg,
        "conn": conn,
        "audio_path": Path(audio_path),
        "source": Path(doc.get("audio_path") or doc["audio_file"]).name,
        "samples": pick_samples(doc),
        "assigned": {},
        "lock": threading.Lock(),
    }

    server = ThreadingHTTPServer(("127.0.0.1", port), partial(_Handler, ctx=ctx))
    ctx["shutdown"] = server.shutdown

    url = f"http://127.0.0.1:{port}/"
    # flush explicitly: stdout is block-buffered when piped, and a URL that
    # only appears once the server exits is useless.
    print(f"[label] {url}", flush=True)
    print("[label] name each cluster, then hit Done (or Ctrl-C here)", flush=True)
    if open_browser and not os.environ.get("SKIPCAST_NO_BROWSER"):
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        named = {k: v for k, v in ctx["assigned"].items() if v and v != identity.IGNORE}
        if named:
            print(f"[label] saved {len(named)} profile(s): " + ", ".join(sorted(set(named.values()))))
        else:
            print("[label] nothing saved")
        conn.close()


def render_page(ctx) -> str:
    return _PAGE.replace("__STATE__", json.dumps(build_state(ctx)).replace("</", "<\\/"))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>skipcast — label speakers</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
          --fg:#e7eaf0; --muted:#99a1b3; --accent:#4f9cf9; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f7f9; --panel:#fff; --panel2:#f0f2f5; --line:#dfe3ea;
            --fg:#14171d; --muted:#626c7f; --accent:#1f6feb; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  .wrap { max-width:960px; margin:0 auto; padding:20px 20px 60px; }
  h1 { font-size:18px; margin:0 0 2px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px;
           padding:14px; margin-bottom:14px; }
  .panel h2 { font-size:11px; text-transform:uppercase; letter-spacing:.08em;
              color:var(--muted); margin:0 0 10px; }
  .row { display:flex; gap:12px; align-items:center; padding:10px 0;
         border-bottom:1px solid var(--line); flex-wrap:wrap; }
  .row:last-child { border-bottom:none; }
  .swatch { width:12px; height:28px; border-radius:3px; flex:none; }
  .who { flex:none; width:120px; font-weight:600; font-variant-numeric:tabular-nums; }
  .stats { flex:none; width:150px; color:var(--muted); font-size:12.5px;
           font-variant-numeric:tabular-nums; }
  .samples { flex:1 1 200px; display:flex; gap:6px; flex-wrap:wrap; }
  button { background:var(--panel2); color:var(--fg); border:1px solid var(--line);
           border-radius:6px; padding:4px 9px; font-size:12px; cursor:pointer;
           font-family:inherit; }
  button:hover { border-color:var(--accent); }
  button.playing { background:var(--accent); border-color:var(--accent); color:#fff; }
  input[type=text] { background:var(--panel2); color:var(--fg); border:1px solid var(--line);
                     border-radius:6px; padding:5px 8px; font-family:inherit; width:150px; }
  .hint { font-size:11.5px; color:var(--muted); }
  .auto { color:var(--accent); font-size:11.5px; }
  .saved { color:#22c55e; font-size:11.5px; }
  .ignored { opacity:.5; }
  table.spk { width:100%; border-collapse:collapse; }
  table.spk td { padding:6px 4px; border-bottom:1px solid var(--line); }
  table.spk tr:last-child td { border-bottom:none; }
  .done { margin-top:16px; }
  .done button { padding:8px 18px; font-size:14px; background:var(--accent);
                 border-color:var(--accent); color:#fff; }
</style></head><body>
<div class="wrap">
  <h1 id="title"></h1>
  <div class="sub" id="sub"></div>

  <div class="panel">
    <h2>Clusters — play a sample, then name it</h2>
    <div id="clusters"></div>
    <div class="hint" style="margin-top:10px">
      Type a name and press Enter to save. Leave blank and press Enter to clear.
      Use <b>ignore</b> for intro music, ad reads and crosstalk fragments.
    </div>
  </div>

  <div class="panel">
    <h2>Known speakers — tick skip to cut them from future episodes</h2>
    <table class="spk"><tbody id="speakers"></tbody></table>
  </div>

  <div class="done"><button id="donebtn">Done</button></div>
</div>
<audio id="player"></audio>
<script>
let STATE = __STATE__;
const audio = document.getElementById('player');
let stopAt = null, playingBtn = null;

audio.addEventListener('timeupdate', () => {
  if (stopAt !== null && audio.currentTime >= stopAt) {
    audio.pause();
    stopAt = null;
    if (playingBtn) { playingBtn.classList.remove('playing'); playingBtn = null; }
  }
});

function fmt(s) {
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m ? `${m}m ${sec}s` : `${sec}s`;
}
function clock(t) {
  const h = Math.floor(t/3600), m = Math.floor(t%3600/60), s = Math.floor(t%60);
  return (h ? h + ':' + String(m).padStart(2,'0') : String(m)) + ':' + String(s).padStart(2,'0');
}

function play(btn, start, end) {
  if (playingBtn) playingBtn.classList.remove('playing');
  if (playingBtn === btn && !audio.paused) { audio.pause(); playingBtn = null; return; }
  if (!audio.src) audio.src = '/audio';
  audio.currentTime = start;
  stopAt = end;
  playingBtn = btn;
  btn.classList.add('playing');
  audio.play().catch(e => console.error(e));
}

async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const j = await r.json();
  if (j.error) { alert(j.error); return null; }
  if (j.state) { STATE = j.state; render(); }
  return j;
}

function render() {
  document.getElementById('title').textContent = STATE.title;
  document.getElementById('sub').textContent =
    `${STATE.clusters.length} clusters · match threshold ${STATE.threshold}`;

  const box = document.getElementById('clusters');
  box.innerHTML = '';
  for (const c of STATE.clusters) {
    const div = document.createElement('div');
    div.className = 'row' + (c.assigned === '__ignore__' ? ' ignored' : '');

    const sw = document.createElement('div');
    sw.className = 'swatch'; sw.style.background = c.color;

    const who = document.createElement('div');
    who.className = 'who'; who.textContent = c.cluster_label;

    const st = document.createElement('div');
    st.className = 'stats';
    st.textContent = `${fmt(c.total_seconds)} · ${(c.share*100).toFixed(1)}% · ${c.segment_count} seg`;

    const samples = document.createElement('div');
    samples.className = 'samples';
    if (!c.samples.length) samples.textContent = '(no samples)';
    for (const s of c.samples) {
      const b = document.createElement('button');
      b.textContent = `▶ ${clock(s.start)} (${s.duration}s)`;
      b.onclick = () => play(b, s.start, s.end);
      samples.appendChild(b);
    }

    const nameWrap = document.createElement('div');
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = 'name…';
    input.setAttribute('list', 'known');
    input.value = (c.assigned && c.assigned !== '__ignore__') ? c.assigned
                : (c.auto_name || '');
    input.onkeydown = e => {
      if (e.key === 'Enter') post('/api/label', {cluster_label: c.cluster_label, name: input.value});
    };
    const ign = document.createElement('button');
    ign.textContent = 'ignore';
    ign.style.marginLeft = '6px';
    ign.onclick = () => post('/api/label', {cluster_label: c.cluster_label, name: '__ignore__'});
    nameWrap.appendChild(input);
    nameWrap.appendChild(ign);

    const note = document.createElement('div');
    note.style.flexBasis = '100%';
    note.style.paddingLeft = '24px';
    if (!c.has_embedding) {
      note.innerHTML = '<span class="hint">no embedding — too little clean speech to profile</span>';
    } else if (c.assigned && c.assigned !== '__ignore__') {
      note.innerHTML = `<span class="saved">saved as ${c.assigned}</span>`;
    } else if (c.auto_name) {
      note.innerHTML = `<span class="auto">auto-matched ${c.auto_name} at ${c.similarity}` +
        (c.runner_up ? ` (next: ${c.runner_up} ${c.runner_up_similarity})` : '') +
        `</span> <span class="hint">— press Enter to confirm</span>`;
    } else if (c.closest_name) {
      note.innerHTML = `<span class="hint">closest known voice ${c.closest_name} ` +
        `at ${c.similarity}, below the ${STATE.threshold} threshold</span>`;
    }

    div.append(sw, who, st, samples, nameWrap, note);
    box.appendChild(div);
  }

  const dl = document.getElementById('known') || document.createElement('datalist');
  dl.id = 'known'; dl.innerHTML = '';
  for (const s of STATE.speakers) {
    const o = document.createElement('option'); o.value = s.name; dl.appendChild(o);
  }
  document.body.appendChild(dl);

  const tb = document.getElementById('speakers');
  tb.innerHTML = '';
  if (!STATE.speakers.length) {
    tb.innerHTML = '<tr><td class="hint">none yet — name a cluster above</td></tr>';
  }
  for (const s of STATE.speakers) {
    const tr = document.createElement('tr');
    const cb = `<input type="checkbox" data-name="${s.name}" ${s.skip ? 'checked' : ''}>`;
    tr.innerHTML = `<td><b>${s.name}</b></td>` +
      `<td class="hint">${s.profiles} voice sample(s)</td>` +
      `<td style="width:1%"><label>${cb} skip</label></td>`;
    tb.appendChild(tr);
  }
  tb.onchange = e => {
    const name = e.target.dataset.name;
    if (name) post('/api/skip', {name, skip: e.target.checked});
  };
}

document.getElementById('donebtn').onclick = async () => {
  await post('/api/done', {});
  document.body.innerHTML =
    '<div class="wrap"><h1>Saved.</h1><div class="sub">You can close this tab.</div></div>';
};

render();
</script></body></html>
"""
