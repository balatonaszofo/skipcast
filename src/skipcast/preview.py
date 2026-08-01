"""Self-contained preview HTML.

No CDN, no build step, no network at runtime. The audio itself is referenced
by relative path rather than inlined — base64ing an hour of MP3 into the page
would produce a 60 MB file that no browser enjoys. Keep the .mp3 next to the
.preview.html and it plays; if the browser blocks the local file, the page has
a file picker fallback.

The page also mirrors the Phase 2 segment-selection rules in JavaScript so you
can hear what a cut would sound like (it seeks over cut regions) before any
cutting code exists. It skips rather than crossfades, so the real output will
sound marginally smoother than the preview, never worse.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from urllib.parse import quote

from .config import Config

# Distinguishable at a glance, and still distinguishable when dimmed.
PALETTE = [
    "#4f9cf9", "#f97316", "#22c55e", "#a855f7",
    "#ef4444", "#14b8a6", "#eab308", "#ec4899",
    "#6366f1", "#84cc16", "#06b6d4", "#f43f5e",
]

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — skipcast preview</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --panel-2: #1e222b; --line: #2a2f3a;
    --fg: #e7eaf0; --muted: #99a1b3; --accent: #4f9cf9;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f6f7f9; --panel: #ffffff; --panel-2: #f0f2f5; --line: #dfe3ea;
      --fg: #14171d; --muted: #626c7f; --accent: #1f6feb;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 20px 20px 60px; }
  h1 { font-size: 18px; margin: 0 0 2px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .panel {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: 14px; margin-bottom: 14px;
  }
  .panel h2 {
    font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 0 0 10px; font-weight: 600;
  }
  audio { width: 100%; margin-bottom: 10px; }
  #timeline { width: 100%; height: 76px; display: block; cursor: pointer; border-radius: 6px; }
  .ticks { display: flex; justify-content: space-between; color: var(--muted); font-size: 11px; margin-top: 4px; }

  table.speakers { width: 100%; border-collapse: collapse; }
  table.speakers td { padding: 7px 6px; border-bottom: 1px solid var(--line); vertical-align: middle; }
  table.speakers tr:last-child td { border-bottom: none; }
  .swatch { width: 13px; height: 13px; border-radius: 3px; display: inline-block; vertical-align: -2px; }
  .spk-name { font-weight: 600; }
  .num { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); white-space: nowrap; }
  .bar { background: var(--panel-2); border-radius: 3px; height: 7px; width: 100%; min-width: 60px; overflow: hidden; }
  .bar > i { display: block; height: 100%; }

  button {
    background: var(--panel-2); color: var(--fg); border: 1px solid var(--line);
    border-radius: 6px; padding: 4px 9px; font-size: 12px; cursor: pointer; font-family: inherit;
  }
  button:hover { border-color: var(--accent); }
  button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
  label.chk { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; user-select: none; }
  input[type=number] {
    width: 68px; background: var(--panel-2); color: var(--fg);
    border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px; font-family: inherit;
  }
  .controls { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; }
  .controls .field { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }

  .verdict { margin-top: 12px; padding: 10px 12px; border-radius: 8px; background: var(--panel-2); font-size: 13px; }
  .verdict b { font-variant-numeric: tabular-nums; }
  .warn { color: #f97316; }
  .danger { color: #ef4444; }

  .seglist { max-height: 340px; overflow-y: auto; border: 1px solid var(--line); border-radius: 8px; }
  table.segs { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  table.segs td { padding: 4px 8px; border-bottom: 1px solid var(--line); font-size: 12.5px; }
  table.segs tr { cursor: pointer; content-visibility: auto; contain-intrinsic-size: 26px; }
  table.segs tr:hover td { background: var(--panel-2); }
  table.segs tr.playing td { background: color-mix(in srgb, var(--accent) 22%, transparent); }
  table.segs tr.cut td { opacity: .42; text-decoration: line-through; }
  .kbd { color: var(--muted); font-size: 12px; margin-top: 10px; }
  .kbd code {
    background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 4px; padding: 1px 5px; font-size: 11px;
  }
  #loaderr { display: none; color: #f97316; font-size: 12.5px; margin-bottom: 8px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <div class="sub" id="sub"></div>
  <div class="sub" id="src" style="margin-top:-12px"></div>

  <div class="panel">
    <div id="loaderr">
      Couldn't load <code>__AUDIO_SRC__</code> from this page.
      Keep the audio file in the same folder as this HTML, or
      <input type="file" id="picker" accept="audio/*">
    </div>
    <audio id="player" controls preload="metadata" src="__AUDIO_SRC__"></audio>
    <canvas id="timeline"></canvas>
    <div class="ticks" id="ticks"></div>
    <div class="kbd">
      <code>space</code> play/pause &nbsp; <code>←</code>/<code>→</code> 5s &nbsp;
      <code>[</code>/<code>]</code> prev/next segment &nbsp; <code>s</code> toggle skip preview
    </div>
  </div>

  <div class="panel">
    <h2>Speakers — check "skip" to hear the episode without them</h2>
    <table class="speakers"><tbody id="spk"></tbody></table>
  </div>

  <div class="panel">
    <h2>Cut rules (same rules Phase 2 will apply)</h2>
    <div class="controls">
      <label class="chk"><input type="checkbox" id="livecut"> <span>Skip preview during playback</span></label>
      <div class="field">min skip <input type="number" id="minskip" min="0" step="1" value="__MIN_SKIP__"> s</div>
      <div class="field">merge gap <input type="number" id="mergegap" min="0" step="0.1" value="__MERGE_GAP__"> s</div>
      <div class="field">boundary pad <input type="number" id="pad" min="0" step="0.05" value="__PAD__"> s</div>
    </div>
    <div class="verdict" id="verdict"></div>
  </div>

  <div class="panel">
    <h2>Segments <span id="segcount" style="color:var(--muted);font-weight:400"></span></h2>
    <div class="seglist"><table class="segs"><tbody id="segs"></tbody></table></div>
  </div>
</div>

<script>
const DATA = __DATA__;
const audioEl = document.getElementById('player');
const segs = DATA.segments;
const speakers = DATA.speakers;
const colorOf = {};
speakers.forEach(s => colorOf[s.speaker_label] = s.color);
const skip = new Set();

function fmt(t) {
  t = Math.max(0, t);
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = Math.floor(t % 60);
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(s).padStart(2, '0');
}
function fmtLong(t) {
  const m = Math.floor(t / 60), s = Math.round(t % 60);
  return m ? `${m}m ${s}s` : `${s}s`;
}

document.getElementById('sub').textContent =
  `${DATA.audio_file} · ${fmt(DATA.duration)} · ${speakers.length} speaker clusters · ` +
  `${segs.length} segments · ${DATA.pipeline}`;

if (DATA.source_url) {
  const a = document.createElement('a');
  a.href = DATA.source_url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.style.color = 'var(--accent)';
  a.textContent = DATA.source_url;
  const src = document.getElementById('src');
  src.append(DATA.uploader ? DATA.uploader + ' · ' : '', a);
}

/* ---- Phase 2 segment-selection rules, mirrored ---------------------------
   1. merge adjacent same-speaker segments with a gap under mergeGap
   2. keep only merged regions at least minSkip long
   3. pad each boundary inward so we don't clip the kept speaker
   4. union what's left (speakers can overlap)
   Crossfade is not modelled here — the preview jumps, the real cut blends. */
function computeCuts() {
  const mergeGap = +document.getElementById('mergegap').value;
  const minSkip  = +document.getElementById('minskip').value;
  const pad      = +document.getElementById('pad').value;

  let regions = [];
  for (const label of skip) {
    const mine = segs.filter(s => s.speaker_label === label);
    let cur = null;
    for (const s of mine) {
      if (cur && s.start - cur.end < mergeGap) {
        cur.end = Math.max(cur.end, s.end);
      } else {
        if (cur) regions.push(cur);
        cur = { start: s.start, end: s.end, speaker_label: label };
      }
    }
    if (cur) regions.push(cur);
  }

  // No kept speaker exists against the file boundaries, so padding there would
  // only strand a sliver of the removed voice. Matches cut.py.
  const dur = DATA.duration, edge = DATA.crossfade;
  regions = regions
    .filter(r => r.end - r.start >= minSkip)
    .map(r => ({
      ...r,
      start: r.start < edge ? 0 : r.start + pad,
      end: r.end > dur - edge ? dur : r.end - pad,
    }))
    .filter(r => r.end > r.start)
    .sort((a, b) => a.start - b.start);

  const merged = [];
  for (const r of regions) {
    const last = merged[merged.length - 1];
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else merged.push({ start: r.start, end: r.end });
  }
  return merged;
}

let cuts = [];
function cutAt(t) {
  for (const c of cuts) { if (t >= c.start && t < c.end) return c; if (c.start > t) break; }
  return null;
}

function refresh() {
  cuts = skip.size ? computeCuts() : [];
  const total = cuts.reduce((a, c) => a + (c.end - c.start), 0);
  const frac = DATA.duration ? total / DATA.duration : 0;
  const v = document.getElementById('verdict');

  if (!skip.size) {
    v.innerHTML = 'No speakers marked skip. Check one above, then turn on ' +
      '<b>Skip preview</b> and listen to a stretch where they hand off.';
  } else {
    const cls = frac > DATA.max_skip_fraction ? 'danger' : (frac > 0.35 ? 'warn' : '');
    let msg = `Would remove <b>${cuts.length}</b> regions, <b>${fmtLong(total)}</b> ` +
      `(<b class="${cls}">${(frac * 100).toFixed(1)}%</b> of the episode). ` +
      `Result: <b>${fmt(DATA.duration - total)}</b> long.`;
    if (frac > DATA.max_skip_fraction) {
      msg += `<br><span class="danger">Over the ${(DATA.max_skip_fraction * 100).toFixed(0)}% ` +
        `max_skip_fraction ceiling — Phase 2 would refuse this episode rather than emit it.</span>`;
    }
    v.innerHTML = msg;
  }
  drawBase();
  markCutRows();
}

/* ---- timeline ---------------------------------------------------------- */
const canvas = document.getElementById('timeline');
const ctx = canvas.getContext('2d');
const LANE_TOP = 8, LANE_H = 34, CUT_TOP = 50, CUT_H = 14;

// The lanes only change when the skip set or the cut rules change, so they are
// rendered once into an offscreen canvas and blitted each frame. Redrawing a
// few thousand segments at 60fps for the playhead alone is not worth it.
const base = document.createElement('canvas');
const bctx = base.getContext('2d');

function sizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = 76;
  canvas.width = base.width = Math.max(1, Math.round(w * dpr));
  canvas.height = base.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawBase();
}

function drawBase() {
  const w = canvas.clientWidth, dur = DATA.duration || 1;
  const css = getComputedStyle(document.documentElement);
  bctx.clearRect(0, 0, w, 76);
  bctx.fillStyle = css.getPropertyValue('--panel-2');
  bctx.fillRect(0, LANE_TOP, w, LANE_H);

  for (const s of segs) {
    const x = s.start / dur * w;
    const bw = Math.max(1, (s.end - s.start) / dur * w);
    bctx.globalAlpha = skip.size && !skip.has(s.speaker_label) ? 0.32 : 1;
    bctx.fillStyle = colorOf[s.speaker_label] || '#888';
    bctx.fillRect(x, LANE_TOP, bw, LANE_H);
  }
  bctx.globalAlpha = 1;

  bctx.fillStyle = css.getPropertyValue('--panel-2');
  bctx.fillRect(0, CUT_TOP, w, CUT_H);
  bctx.fillStyle = '#ef4444';
  for (const c of cuts) {
    bctx.fillRect(c.start / dur * w, CUT_TOP, Math.max(1, (c.end - c.start) / dur * w), CUT_H);
  }
  drawTimeline();
}

function drawTimeline() {
  const w = canvas.clientWidth, dur = DATA.duration || 1;
  ctx.clearRect(0, 0, w, 76);
  ctx.drawImage(base, 0, 0, w, 76);
  const px = (audioEl.currentTime || 0) / dur * w;
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--fg');
  ctx.fillRect(px - 1, 2, 2, 72);
}

canvas.addEventListener('click', e => {
  const r = canvas.getBoundingClientRect();
  seek((e.clientX - r.left) / r.width * DATA.duration);
});

/* ---- speaker table ------------------------------------------------------ */
const spkBody = document.getElementById('spk');
speakers.forEach(s => {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td style="width:1%"><span class="swatch" style="background:${s.color}"></span></td>
    <td class="spk-name">${s.matched_name || s.speaker_label}${
        s.matched_name ? ` <span style="font-weight:400;color:var(--muted);font-size:11.5px">`
                       + `${s.speaker_label} · ${s.similarity.toFixed(2)}</span>` : ''}</td>
    <td style="width:32%"><div class="bar"><i style="width:${(s.share * 100).toFixed(1)}%;background:${s.color}"></i></div></td>
    <td class="num">${fmtLong(s.total_seconds)}</td>
    <td class="num">${(s.share * 100).toFixed(1)}%</td>
    <td class="num">${s.segment_count} seg</td>
    <td style="width:1%"><button data-jump="${s.speaker_label}">hear</button></td>
    <td style="width:1%"><label class="chk"><input type="checkbox" data-skip="${s.speaker_label}"${
        s.skip ? ' checked' : ''}> skip</label></td>`;
  spkBody.appendChild(tr);
  // A speaker already flagged skip in the database starts ticked, so the page
  // opens showing what the pipeline would actually do.
  if (s.skip) skip.add(s.speaker_label);
});

spkBody.addEventListener('change', e => {
  const label = e.target.dataset.skip;
  if (!label) return;
  e.target.checked ? skip.add(label) : skip.delete(label);
  refresh();
});

spkBody.addEventListener('click', e => {
  const label = e.target.dataset.jump;
  if (!label) return;
  // Longest segment for this speaker: the clearest sample of the voice.
  const best = segs.filter(s => s.speaker_label === label)
                   .sort((a, b) => (b.end - b.start) - (a.end - a.start))[0];
  if (best) { seek(best.start + 0.2); audioEl.play().catch(() => {}); }
});

/* ---- segment list ------------------------------------------------------- */
const segBody = document.getElementById('segs');
document.getElementById('segcount').textContent = `(click to jump)`;
const rows = segs.map((s, i) => {
  const tr = document.createElement('tr');
  tr.dataset.i = i;
  tr.innerHTML = `
    <td style="width:1%"><span class="swatch" style="background:${colorOf[s.speaker_label]}"></span></td>
    <td style="width:16%">${s.speaker_label}</td>
    <td style="width:14%">${fmt(s.start)}</td>
    <td style="width:14%">${fmt(s.end)}</td>
    <td class="num" style="width:14%">${(s.end - s.start).toFixed(1)}s</td>
    <td></td>`;
  segBody.appendChild(tr);
  return tr;
});
segBody.addEventListener('click', e => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  seek(segs[+tr.dataset.i].start + 0.05);
  audioEl.play().catch(() => {});
});

// Both lists are sorted, so sweep them together rather than probing each row
// against every cut region — an hour and a half of audio is a few thousand rows.
function markCutRows() {
  let c = 0;
  for (let i = 0; i < segs.length; i++) {
    const mid = (segs[i].start + segs[i].end) / 2;
    while (c < cuts.length && cuts[c].end <= mid) c++;
    rows[i].classList.toggle('cut', c < cuts.length && mid >= cuts[c].start);
  }
}

/* ---- playback ----------------------------------------------------------- */
let playingRow = -1;
function seek(t){ audioEl.currentTime = Math.max(0, Math.min(DATA.duration - 0.05, t)); drawTimeline(); }

audioEl.addEventListener('timeupdate', () => {
  if (document.getElementById('livecut').checked) {
    const c = cutAt(audioEl.currentTime);
    if (c) { audioEl.currentTime = c.end; return; }
  }
  const t = audioEl.currentTime;
  const i = segs.findIndex(s => t >= s.start && t < s.end);
  if (i === playingRow) return;          // nothing moved, don't touch the DOM
  if (playingRow >= 0) rows[playingRow].classList.remove('playing');
  playingRow = i;
  if (i >= 0) {
    rows[i].classList.add('playing');
    rows[i].scrollIntoView({ block: 'nearest' });
  }
});

audioEl.addEventListener('error', () => { document.getElementById('loaderr').style.display = 'block'; });
document.getElementById('picker').addEventListener('change', e => {
  const f = e.target.files[0];
  if (f) { audioEl.src = URL.createObjectURL(f); document.getElementById('loaderr').style.display = 'none'; }
});

document.addEventListener('keydown', e => {
  if (e.target.matches('input, button')) return;
  const t = audioEl.currentTime;
  if (e.code === 'Space') { e.preventDefault(); audioEl.paused ? audioEl.play() : audioEl.pause(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); seek(t + 5); }
  else if (e.key === 'ArrowLeft')  { e.preventDefault(); seek(t - 5); }
  else if (e.key === ']') { const n = segs.find(s => s.start > t + 0.01); if (n) seek(n.start + 0.05); }
  else if (e.key === '[') { const p = [...segs].reverse().find(s => s.start < t - 0.6); if (p) seek(p.start + 0.05); }
  else if (e.key === 's') { const b = document.getElementById('livecut'); b.checked = !b.checked; }
});

['minskip', 'mergegap', 'pad'].forEach(id =>
  document.getElementById(id).addEventListener('input', refresh));

// Ticks along the timeline.
const ticks = document.getElementById('ticks');
for (let i = 0; i <= 6; i++) {
  const d = document.createElement('span');
  d.textContent = fmt(DATA.duration * i / 6);
  ticks.appendChild(d);
}

window.addEventListener('resize', sizeCanvas);
sizeCanvas();
refresh();
(function tick() { if (!audioEl.paused) drawTimeline(); requestAnimationFrame(tick); })();
</script>
</body>
</html>
"""


def render(doc: dict, cfg: Config, audio_src: str | None = None) -> str:
    """Build the preview page for a segments document."""
    speakers = [
        {
            "speaker_label": s["speaker_label"],
            "total_seconds": s["total_seconds"],
            "segment_count": s["segment_count"],
            "share": s["share"],
            "matched_name": s.get("matched_name"),
            "similarity": s.get("similarity", 0.0),
            "skip": bool(s.get("skip")),
            "color": PALETTE[i % len(PALETTE)],
        }
        for i, s in enumerate(doc["speakers"])
    ]  # embeddings are deliberately not shipped to the page — 8 x 256 floats
       # of payload the browser has no use for
    payload = {
        "audio_file": doc["audio_file"],
        "duration": doc["duration"],
        "pipeline": doc["pipeline"],
        "source_url": doc.get("source_url"),
        "uploader": doc.get("uploader"),
        "segments": doc["segments"],
        "speakers": speakers,
        "max_skip_fraction": cfg.cut.max_skip_fraction,
        "crossfade": cfg.cut.crossfade_seconds,
    }
    # </ inside a <script> would close the tag early.
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    src = audio_src if audio_src is not None else quote(doc["audio_file"])
    title = escape(doc.get("title") or doc["audio_file"])
    html = _TEMPLATE
    for needle, value in (
        ("__TITLE__", title),
        ("__AUDIO_SRC__", src),
        ("__MIN_SKIP__", str(cfg.cut.min_skip_seconds)),
        ("__MERGE_GAP__", str(cfg.cut.merge_gap_seconds)),
        ("__PAD__", str(cfg.cut.boundary_padding_seconds)),
        ("__DATA__", data),
    ):
        html = html.replace(needle, value)
    return html


def write(doc: dict, cfg: Config, dest: Path, audio_src: str | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(doc, cfg, audio_src), encoding="utf-8")
    return dest
