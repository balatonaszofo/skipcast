"""The control panel served at /.

One page, vanilla JS, no build step and no CDN. Laid out for a phone first
because that is where it gets used — the desktop is meant to become a box you
never touch.
"""

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0f1115">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon.svg">
<title>skipcast</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
          --fg:#e7eaf0; --muted:#98a1b3; --accent:#4f9cf9; --ok:#22c55e;
          --warn:#f97316; --bad:#ef4444; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f7f9; --panel:#fff; --panel2:#eef1f5; --line:#dfe3ea;
            --fg:#14171d; --muted:#5c6675; --accent:#1f6feb; }
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         padding-bottom:env(safe-area-inset-bottom); }
  header { position:sticky; top:0; z-index:20; background:var(--panel);
           border-bottom:1px solid var(--line); padding:12px 16px 0;
           padding-top:calc(12px + env(safe-area-inset-top)); }
  h1 { font-size:17px; margin:0 0 10px; font-weight:650; letter-spacing:-0.01em; }
  h1 span { color:var(--muted); font-weight:400; font-size:13px; }
  nav { display:flex; gap:4px; overflow-x:auto; }
  nav button { flex:none; background:none; border:none; color:var(--muted);
    padding:9px 13px; font:inherit; font-size:14px; border-bottom:2px solid transparent;
    cursor:pointer; }
  nav button.on { color:var(--fg); border-bottom-color:var(--accent); font-weight:600; }
  nav .badge { background:var(--accent); color:#fff; border-radius:9px;
    padding:0 6px; font-size:11px; margin-left:5px; }
  main { padding:16px; max-width:820px; margin:0 auto; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px;
          padding:14px; margin-bottom:12px; }
  .card h2 { font-size:11px; text-transform:uppercase; letter-spacing:.07em;
             color:var(--muted); margin:0 0 10px; font-weight:650; }
  .row { display:flex; align-items:center; gap:10px; }
  .grow { flex:1; min-width:0; }
  .title { font-weight:600; overflow:hidden; text-overflow:ellipsis;
           white-space:nowrap; }
  .sub { color:var(--muted); font-size:13px; }
  .wrapline { white-space:normal; }
  button.btn, a.btn { display:inline-block; background:var(--panel2); color:var(--fg);
    border:1px solid var(--line); border-radius:9px; padding:9px 13px; font:inherit;
    font-size:14px; cursor:pointer; text-decoration:none; }
  button.btn:active { transform:scale(.97); }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.danger { color:var(--bad); }
  button.btn:disabled { opacity:.45; }
  input[type=text], input[type=search], select { width:100%; background:var(--panel2);
    color:var(--fg); border:1px solid var(--line); border-radius:9px; padding:11px;
    font:inherit; font-size:16px; }
  select { appearance:none; -webkit-appearance:none; }
  .pill { font-size:11px; padding:2px 8px; border-radius:99px; background:var(--panel2);
    color:var(--muted); border:1px solid var(--line); white-space:nowrap; }
  .pill.ready { color:var(--ok); } .pill.failed, .pill.refused { color:var(--bad); }
  .pill.running { color:var(--accent); } .pill.pending, .pill.queued { color:var(--warn); }
  .item { padding:11px 0; border-bottom:1px solid var(--line); }
  .item:last-child { border-bottom:none; }
  .swatch { width:10px; height:34px; border-radius:3px; flex:none; }
  .muted { color:var(--muted); }
  .stack { display:flex; flex-direction:column; gap:9px; }
  .wrap { display:flex; flex-wrap:wrap; gap:8px; }
  pre { background:var(--panel2); border-radius:9px; padding:11px; overflow-x:auto;
        font-size:11.5px; line-height:1.45; max-height:260px; overflow-y:auto;
        margin:0; white-space:pre-wrap; word-break:break-word; }
  code { background:var(--panel2); padding:2px 6px; border-radius:5px; font-size:13px;
         word-break:break-all; }
  .switch { position:relative; width:48px; height:29px; flex:none; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:var(--panel2); border:1px solid var(--line);
    border-radius:99px; transition:.15s; }
  .slider:before { content:""; position:absolute; height:21px; width:21px; left:3px;
    bottom:3px; background:var(--fg); border-radius:50%; transition:.15s; }
  .switch input:checked + .slider { background:var(--bad); border-color:var(--bad); }
  .switch input:checked + .slider:before { transform:translateX(19px); background:#fff; }
  .empty { color:var(--muted); text-align:center; padding:26px 10px; font-size:14px; }
  .md { font-size:14.5px; line-height:1.6; }
  .md h3 { font-size:15px; margin:16px 0 6px; }
  .md h4 { font-size:14px; margin:14px 0 5px; color:var(--accent); }
  .md ul { margin:6px 0; padding-left:20px; }
  .md li { margin-bottom:5px; }
  .toast { position:fixed; left:50%; transform:translateX(-50%); bottom:24px;
    background:var(--fg); color:var(--bg); padding:11px 17px; border-radius:10px;
    font-size:14px; z-index:100; max-width:88%; }
  .back { color:var(--accent); cursor:pointer; font-size:14px; margin-bottom:12px;
    display:inline-block; }
  img.art { width:56px; height:56px; border-radius:9px; flex:none; background:var(--panel2); }
  .spin { display:inline-block; width:13px; height:13px; border:2px solid var(--line);
    border-top-color:var(--accent); border-radius:50%; animation:sp .8s linear infinite;
    vertical-align:-2px; }
  @keyframes sp { to { transform:rotate(360deg); } }

  /* ---- player ---- */
  .prog { height:4px; background:var(--panel2); border-radius:2px; overflow:hidden;
          margin-top:7px; }
  .prog > i { display:block; height:100%; background:var(--accent); }
  #mini { position:fixed; left:0; right:0; bottom:0; z-index:50; background:var(--panel);
    border-top:1px solid var(--line); padding:10px 14px;
    padding-bottom:calc(10px + env(safe-area-inset-bottom)); display:none; }
  #mini.on { display:block; }
  #mini .mtitle { font-size:13.5px; font-weight:600; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  #mini .msub { font-size:11.5px; color:var(--muted); font-variant-numeric:tabular-nums; }
  #mini .controls { display:flex; align-items:center; gap:8px; margin-top:8px; }
  #mini button { background:var(--panel2); border:1px solid var(--line); color:var(--fg);
    border-radius:10px; padding:9px 12px; font:inherit; font-size:13px; cursor:pointer; }
  #mini button.play { background:var(--accent); border-color:var(--accent); color:#fff;
    min-width:56px; font-size:16px; }
  #scrub { flex:1; -webkit-appearance:none; appearance:none; height:20px;
    background:transparent; }
  #scrub::-webkit-slider-runnable-track { height:4px; background:var(--panel2);
    border-radius:2px; }
  #scrub::-webkit-slider-thumb { -webkit-appearance:none; width:15px; height:15px;
    border-radius:50%; background:var(--accent); margin-top:-5.5px; }
  #scrub::-moz-range-track { height:4px; background:var(--panel2); border-radius:2px; }
  #scrub::-moz-range-thumb { width:15px; height:15px; border:none; border-radius:50%;
    background:var(--accent); }
  body.playing main { padding-bottom:130px; }
</style></head>
<body>
<header>
  <h1>skipcast <span id="hdr"></span></h1>
  <nav>
    <button data-tab="listen" class="on">Listen</button>
    <button data-tab="search">Search</button>
    <button data-tab="feeds">Podcasts</button>
    <button data-tab="add">Add</button>
    <button data-tab="speakers">Speakers</button>
    <button data-tab="people">People</button>
    <button data-tab="jobs">Activity<span class="badge" id="jobbadge" style="display:none"></span></button>
  </nav>
</header>
<main id="main"></main>
<audio id="player" preload="metadata"></audio>

<div id="mini">
  <div class="mtitle" id="m-title"></div>
  <div class="msub" id="m-sub"></div>
  <div class="controls">
    <button onclick="seekBy(-15)" aria-label="back 15 seconds">−15</button>
    <button class="play" id="m-play" onclick="togglePlay()">▶</button>
    <button onclick="seekBy(30)" aria-label="forward 30 seconds">+30</button>
    <button id="m-rate" onclick="cycleRate()">1×</button>
    <button onclick="closePlayer()" aria-label="close">✕</button>
  </div>
  <input type="range" id="scrub" min="0" max="1000" value="0" style="width:100%">
</div>

<script>
let STATE = null, TAB = 'feeds', VIEW = null, TIMER = null;
const el = document.getElementById('main');
const audio = document.getElementById('player');
let stopAt = null, playingBtn = null;

/* ---- helpers ---------------------------------------------------------- */
const esc = s => (s ?? '').toString().replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mins = s => s ? (s >= 3600 ? `${Math.floor(s/3600)}h ${Math.round(s%3600/60)}m`
                                 : `${Math.round(s/60)}m`) : '0m';
/* Just enough Markdown for the summary's headings, bullets and bold. */
function md(src) {
  return esc(src)
    .replace(/^### (.*)$/gm, '<h4>$1</h4>')
    .replace(/^## (.*)$/gm, '<h3>$1</h3>')
    .replace(/^# (.*)$/gm, '<h3>$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/^[-*] (.*)$/gm, '<li>$1</li>')
    .replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, '<ul>$1</ul>')
    .replace(/\n{2,}/g, '<br><br>');
}

const clock = t => { const h=Math.floor(t/3600), m=Math.floor(t%3600/60), s=Math.floor(t%60);
  return (h? h+':'+String(m).padStart(2,'0') : String(m))+':'+String(s).padStart(2,'0'); };

function toast(msg, ms = 2600) {
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const d = document.createElement('div');
  d.className = 'toast'; d.textContent = msg;
  document.body.appendChild(d);
  setTimeout(() => d.remove(), ms);
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: {'Content-Type': 'application/json'},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((data && data.detail) || `${r.status}`);
  return data;
}

/* Speaker samples and full episodes share one <audio>. A sample sets stopAt so
   it cuts off after a few seconds; an episode clears it. */
function play(btn, key, start, end) {
  if (playingBtn) playingBtn.classList.remove('primary');
  if (playingBtn === btn && !audio.paused) { audio.pause(); playingBtn = null; return; }
  NOW = null; closeMini();
  const src = `/source/${key}.mp3`;
  if (!audio.src.endsWith(src)) audio.src = src;
  const go = () => { audio.currentTime = start; stopAt = end; audio.play().catch(()=>{}); };
  if (audio.readyState > 0) go();
  else audio.addEventListener('loadedmetadata', go, {once:true});
  playingBtn = btn; btn.classList.add('primary');
}

/* ---- episode player ---------------------------------------------------- */
let NOW = null, RATE = 1, scrubbing = false, lastSave = 0;
const RATES = [1, 1.25, 1.5, 1.75, 2];

/* `at` jumps to a position instead of resuming — a search hit or a topic
   heading. It is already in the edited file's clock; the server does that
   conversion, because only it knows what was cut. */
function playEpisode(key, at) {
  const ep = (LISTEN || []).find(e => e.key === key);
  if (!ep) return toast('That episode is not ready to play');
  if (NOW && NOW.key === key && at == null) { togglePlay(); return; }
  savePosition(true);
  if (NOW && NOW.key === key && at != null) {
    audio.currentTime = at; updateMini();
    if (audio.paused) audio.play().catch(()=>{});
    return;
  }
  NOW = ep;
  stopAt = null;
  if (playingBtn) { playingBtn.classList.remove('primary'); playingBtn = null; }
  audio.src = `/audio/${key}.mp3`;
  audio.playbackRate = RATE;
  const start = at != null ? at : (ep.finished ? 0 : (ep.position || 0));
  const go = () => {
    if (start > 0 && start < (audio.duration || Infinity) - 5) audio.currentTime = start;
    audio.play().catch(e => toast('Playback blocked — tap play again'));
  };
  if (audio.readyState > 0) go();
  else audio.addEventListener('loadedmetadata', go, {once:true});
  document.body.classList.add('playing');
  document.getElementById('mini').classList.add('on');
  document.getElementById('m-title').textContent = ep.title || '';
  setMediaSession(ep);
  updateMini();
  render();
}

function togglePlay() {
  if (!NOW) return;
  audio.paused ? audio.play().catch(()=>{}) : audio.pause();
}
function seekBy(d) {
  if (!NOW) return;
  audio.currentTime = Math.max(0, Math.min((audio.duration || 0) - 1,
                                           audio.currentTime + d));
  updateMini();
}
function cycleRate() {
  RATE = RATES[(RATES.indexOf(RATE) + 1) % RATES.length];
  audio.playbackRate = RATE;
  localStorage.setItem('rate', RATE);
  document.getElementById('m-rate').textContent = RATE + '×';
}
function closePlayer() {
  savePosition(true);
  audio.pause();
  NOW = null;
  closeMini();
  render();
}
function closeMini() {
  document.getElementById('mini').classList.remove('on');
  document.body.classList.remove('playing');
}

function updateMini() {
  if (!NOW) return;
  const cur = audio.currentTime || 0, dur = audio.duration || NOW.result_seconds || 0;
  document.getElementById('m-play').textContent = audio.paused ? '▶' : '❚❚';
  document.getElementById('m-sub').textContent =
    `${clock(cur)} / ${clock(dur)}${dur ? ` · ${clock(Math.max(0, dur - cur))} left` : ''}`;
  if (!scrubbing && dur) {
    document.getElementById('scrub').value = Math.round(cur / dur * 1000);
  }
}

function savePosition(force) {
  if (!NOW) return;
  const now = Date.now();
  if (!force && now - lastSave < 10000) return;
  lastSave = now;
  const cur = audio.currentTime || 0, dur = audio.duration || 0;
  const finished = dur > 0 && cur >= dur - 30;
  NOW.position = cur; NOW.finished = finished;
  const body = JSON.stringify({position: cur, duration: dur, finished});
  const url = `/api/playback/${NOW.key}`;
  // sendBeacon survives the page being backgrounded or closed mid-episode.
  if (navigator.sendBeacon) {
    navigator.sendBeacon(url, new Blob([body], {type: 'application/json'}));
  } else {
    fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                body, keepalive:true}).catch(()=>{});
  }
}

/* Lock screen, bluetooth and car controls. */
function setMediaSession(ep) {
  if (!('mediaSession' in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: ep.title || 'skipcast',
    artist: ep.feed_title || 'skipcast',
    album: ep.cut_speakers ? `${Math.round((ep.cut_seconds||0)/60)} min removed` : '',
    artwork: [{src: '/icon.svg', sizes: '512x512', type: 'image/svg+xml'}],
  });
  const set = (a, fn) => { try { navigator.mediaSession.setActionHandler(a, fn); }
                           catch (e) {} };
  set('play', () => audio.play());
  set('pause', () => audio.pause());
  set('seekbackward', () => seekBy(-15));
  set('seekforward', () => seekBy(30));
  set('seekto', d => { if (d.seekTime != null) { audio.currentTime = d.seekTime; updateMini(); } });
}

audio.addEventListener('timeupdate', () => {
  if (stopAt !== null && audio.currentTime >= stopAt) {
    audio.pause(); stopAt = null;
    if (playingBtn) { playingBtn.classList.remove('primary'); playingBtn = null; }
    return;
  }
  if (NOW) { updateMini(); savePosition(false); }
});
audio.addEventListener('play', () => { updateMini(); if (NOW) setPlaybackState('playing'); });
audio.addEventListener('pause', () => { updateMini(); savePosition(true);
                                        if (NOW) setPlaybackState('paused'); });
audio.addEventListener('ended', () => { savePosition(true); render(); });
audio.addEventListener('error', () => { if (NOW) toast('Could not load that episode'); });
function setPlaybackState(s) {
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = s;
}

document.getElementById('scrub').addEventListener('input', e => {
  scrubbing = true;
  const dur = audio.duration || 0;
  if (dur) document.getElementById('m-sub').textContent =
    `${clock(e.target.value / 1000 * dur)} / ${clock(dur)}`;
});
document.getElementById('scrub').addEventListener('change', e => {
  const dur = audio.duration || 0;
  if (dur) audio.currentTime = e.target.value / 1000 * dur;
  scrubbing = false;
  updateMini();
});
window.addEventListener('pagehide', () => savePosition(true));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') savePosition(true);
});
RATE = parseFloat(localStorage.getItem('rate')) || 1;

/* ---- data ------------------------------------------------------------- */
async function refresh(silent) {
  try {
    STATE = await api('/api/state');
    const running = STATE.jobs.filter(j => j.status === 'running' || j.status === 'queued');
    const b = document.getElementById('jobbadge');
    b.style.display = running.length ? '' : 'none';
    b.textContent = running.length;
    document.getElementById('hdr').textContent =
      `${STATE.feeds.length} podcast${STATE.feeds.length === 1 ? '' : 's'}`;
    loadListen();
    if (!silent) render();
    else if (TAB === 'jobs' || (VIEW && VIEW.kind === 'job')) render();
    // Poll faster while something is working.
    clearInterval(TIMER);
    TIMER = setInterval(() => refresh(true), running.length ? 3000 : 20000);
  } catch (e) { toast('Cannot reach server'); }
}

/* ---- views ------------------------------------------------------------ */
function render() {
  if (VIEW && VIEW.kind === 'feed') return renderFeed(VIEW);
  if (VIEW && VIEW.kind === 'episode') return renderEpisode(VIEW);
  if (VIEW && VIEW.kind === 'job') return renderJob(VIEW);
  if (TAB === 'listen') return renderListen();
  if (TAB === 'search') return renderSearch();
  if (TAB === 'feeds') return renderFeeds();
  if (TAB === 'add') return renderAdd();
  if (TAB === 'speakers') return renderSpeakers();
  if (TAB === 'people') return renderPeople();
  if (TAB === 'jobs') return renderJobs();
}

let LISTEN = null;

async function loadListen() {
  try {
    const d = await api('/api/episodes');
    LISTEN = d.episodes;
    // Keep the playing episode's live position rather than the stored one.
    if (NOW) {
      const fresh = LISTEN.find(e => e.key === NOW.key);
      if (fresh) { fresh.position = NOW.position; NOW = fresh; }
    }
  } catch (e) { LISTEN = []; }
  if (TAB === 'listen' && !VIEW) render();
}

function renderListen() {
  if (LISTEN === null) {
    el.innerHTML = '<div class="empty"><span class="spin"></span></div>';
    return;
  }
  if (!LISTEN.length) {
    el.innerHTML = `<div class="empty">Nothing to listen to yet.<br><br>
      Add a podcast, then fetch an episode.<br><br>
      <button class="btn primary" onclick="go('add')">Add a podcast</button></div>`;
    return;
  }
  el.innerHTML = `<div class="card"><h2>Episodes</h2>` + LISTEN.map(e => {
    const dur = e.result_seconds || 0;
    const pos = e.position || 0;
    const pct = dur ? Math.min(100, pos / dur * 100) : 0;
    const playing = NOW && NOW.key === e.key;
    const left = dur && pos > 30 && !e.finished
      ? `${clock(Math.max(0, dur - pos))} left`
      : (e.finished ? 'played' : clock(dur));
    return `
      <div class="item" onclick="playEpisode('${esc(e.key)}')" style="cursor:pointer">
        <div class="row">
          <div class="grow">
            <div class="title" style="${playing ? 'color:var(--accent)' : ''}">
              ${playing && !audio.paused ? '▶ ' : ''}${esc(e.title)}</div>
            <div class="sub">${esc(e.feed_title || e.feed_slug)} · ${left}
              ${e.cut_seconds ? `· ${mins(e.cut_seconds)} of ${esc(e.cut_speakers)} removed` : ''}</div>
          </div>
        </div>
        ${pct > 1 && !e.finished ? `<div class="prog"><i style="width:${pct}%"></i></div>` : ''}
      </div>`;
  }).join('') + '</div>';
}

/* ---- search ------------------------------------------------------------ */
let SEARCH = {q: '', results: null, busy: false, error: '', count: 0,
              mode: 'words'};
let WATCH = null;

/* Two things to search, and they answer different questions: the transcript
   for what was said, the specifics for what a summary decided was worth
   keeping. Same box, a toggle between them. */
function searchModeHtml() {
  const on = m => SEARCH.mode === m ? 'primary' : '';
  return `<div class="wrap">
    <button class="btn ${on('words')}" onclick="setSearchMode('words')">Words said</button>
    <button class="btn ${on('facts')}" onclick="setSearchMode('facts')">Specifics</button>
  </div>`;
}

function setSearchMode(m) {
  SEARCH.mode = m; SEARCH.results = null; SEARCH.error = '';
  render();
  if (SEARCH.q) doTranscriptSearch();
}

function mentionHtml(r, key) {
  const jump = r.at_cut == null ? ''
    : (r.removed
       ? `<button class="btn" onclick="play(this,'${esc(r.episode_key)}',${r.at_seconds},${r.at_seconds + 25})">
            ▶ ${clock(r.at_seconds)} in the original</button>`
       : `<button class="btn primary"
            onclick="playEpisode('${esc(r.episode_key)}',${r.at_cut})">
            ▶ ${clock(r.at_cut)}</button>`);
  return `
    <div class="item">
      <div class="title wrapline">${esc(r.value)}
        <span class="pill">${esc(r.type)}</span>
        ${r.confidence && r.confidence !== 'firm'
          ? `<span class="pill" style="color:var(--warn)">${esc(r.confidence)}</span>` : ''}
      </div>
      ${r.detail ? `<div class="sub wrapline" style="margin-top:4px">${esc(r.detail)}</div>` : ''}
      <div class="sub" style="margin-top:4px">${r.speaker ? esc(r.speaker) + ' · ' : ''}${esc(r.episode_title)}</div>
      <div class="wrap" style="margin-top:8px">${jump}
        <button class="btn" onclick="openEpisode('${esc(r.episode_key)}')">Episode</button>
      </div>
    </div>`;
}

async function loadWatch() {
  try { WATCH = (await api('/api/watchlist')).watchlist; }
  catch (e) { WATCH = []; }
  if (TAB === 'search' && !VIEW) render();
}

function watchHtml() {
  if (WATCH === null) { loadWatch(); return ''; }
  const totalNew = WATCH.reduce((a, w) => a + w.new, 0);
  return `
    <div class="card">
      <h2>Watching${totalNew ? ` · ${totalNew} new` : ''}</h2>
      ${WATCH.length ? WATCH.map(w => `
        <div class="item">
          <div class="row">
            <div class="grow">
              <div class="title">${esc(w.term)}
                ${w.new ? `<span class="pill" style="color:var(--ok)">${w.new} new</span>` : ''}</div>
              <div class="sub">${w.total} mention${w.total === 1 ? '' : 's'}</div>
            </div>
            <button class="btn danger" onclick='unwatch(${JSON.stringify(w.term)})'>✕</button>
          </div>
          ${w.mentions.slice(0, 3).map(m => `
            <div class="sub wrapline" style="margin-top:6px">
              ${esc(m.value)}${m.detail ? ' — ' + esc(m.detail.slice(0,110)) : ''}
              <a class="back" style="margin:0 0 0 4px"
                 onclick="openEpisode('${esc(m.episode_key)}')">episode</a>
            </div>`).join('')}
        </div>`).join('')
      : '<div class="sub">Nothing watched yet. Add a ticker, a company or a name and skipcast will tell you when a summary mentions it.</div>'}
      <div class="row" style="margin-top:11px">
        <input type="text" class="grow" id="wterm" placeholder="Watch a term…"
               autocapitalize="none">
        <button class="btn" onclick="addWatch(this)">Watch</button>
      </div>
      ${totalNew ? `<button class="btn" style="margin-top:9px"
        onclick="markWatchSeen()">Mark all as seen</button>` : ''}
    </div>`;
}

async function addWatch(btn) {
  const field = document.getElementById('wterm');
  const term = (field ? field.value : '').trim();
  if (!term) { toast('Type something to watch'); return; }
  try {
    await api('/api/watchlist', {method:'POST', body:{term}});
    toast(`Watching ${term}`);
    await loadWatch(); render();
  } catch (e) { toast(e.message); }
}

async function unwatch(term) {
  try {
    await api('/api/watchlist/' + encodeURIComponent(term), {method:'DELETE'});
    await loadWatch(); render();
  } catch (e) { toast(e.message); }
}

async function markWatchSeen() {
  try {
    await api('/api/watchlist/seen', {method:'POST', body:{}});
    await loadWatch(); render();
  } catch (e) { toast(e.message); }
}

function renderSearch() {
  const idx = (STATE && STATE.search) || {passages: 0, episodes: 0};
  const feeds = (STATE.feeds || []).map(
    f => `<option value="${esc(f.slug)}">${esc(f.title || f.slug)}</option>`).join('');
  const words = SEARCH.mode === 'words';
  el.innerHTML = `
    <div class="card">
      <div class="stack">
        ${searchModeHtml()}
        <input type="search" id="sq" placeholder="${words
          ? 'Anything anyone said…' : 'A ticker, a company, a name…'}"
               value="${esc(SEARCH.q)}" autocapitalize="none" autocorrect="off"
               enterkeyhint="search">
        <div class="row">
          ${words ? `<select id="sfeed" class="grow">
            <option value="">All podcasts</option>${feeds}
          </select>` : '<div class="grow"></div>'}
          <button class="btn primary" onclick="doTranscriptSearch()">Search</button>
        </div>
      </div>
      <div class="sub" style="margin-top:9px">
        ${words
          ? (idx.episodes
             ? `${idx.passages.toLocaleString()} passages from ${idx.episodes}
                episode${idx.episodes === 1 ? '' : 's'} indexed.
                Quote a phrase, or end a word with * for a prefix.`
             : 'Nothing is indexed yet — episodes become searchable once transcribed.')
          : 'What the summaries pulled out: tickers, figures, dates, claims. Leave it blank to see everything.'}
        <a class="back" style="margin:0 0 0 6px" onclick="rebuildIndex()">Rebuild</a>
      </div>
    </div>
    <div id="sresults">${searchResultsHtml()}</div>
    ${words ? '' : watchHtml()}`;
  const box = document.getElementById('sq');
  if (box) box.addEventListener('keydown', e => {
    if (e.key === 'Enter') doTranscriptSearch();
  });
  const sel = document.getElementById('sfeed');
  if (sel && SEARCH.feed) sel.value = SEARCH.feed;
}

function searchResultsHtml() {
  if (SEARCH.busy) return '<div class="empty"><span class="spin"></span> Searching…</div>';
  if (SEARCH.error) return `<div class="empty">${esc(SEARCH.error)}</div>`;
  if (SEARCH.results === null) return '';
  if (!SEARCH.results.length) {
    return `<div class="empty">Nothing matched “${esc(SEARCH.q)}”.</div>`;
  }
  if (SEARCH.mode === 'facts') {
    return `<div class="card"><h2>${SEARCH.count} specific${SEARCH.count === 1 ? '' : 's'}</h2>`
      + SEARCH.results.map(r => mentionHtml(r)).join('') + '</div>';
  }
  return `<div class="card"><h2>${SEARCH.count} passage${SEARCH.count === 1 ? '' : 's'}</h2>` +
    SEARCH.results.map(r => `
      <div class="item">
        <div class="sub"><b>${esc(r.speaker)}</b> · ${esc(r.episode_title)}</div>
        <div class="wrapline" style="margin:7px 0; font-size:14.5px">${r.snippet_html}</div>
        <div class="wrap">
          ${r.removed
            /* The moment is in the original but not in the edit — offer the
               source audio rather than a link that silently lands elsewhere. */
            ? `<button class="btn" onclick="play(this,'${esc(r.episode_key)}',${r.start},${r.start + 25})">
                 ▶ ${clock(r.start)} in the original</button>
               <span class="pill">cut from your copy</span>`
            : `<button class="btn primary"
                 onclick="playEpisode('${esc(r.episode_key)}',${r.at_cut})">
                 ▶ ${clock(r.at_cut)}</button>`}
          <button class="btn" onclick="openEpisode('${esc(r.episode_key)}')">Episode</button>
        </div>
      </div>`).join('') + '</div>';
}

async function doTranscriptSearch() {
  const field = document.getElementById('sq');
  const sel = document.getElementById('sfeed');
  const q = (field ? field.value : '').trim();
  SEARCH.feed = sel ? sel.value : '';
  // A blank specifics search is a browse, not a mistake — it lists everything
  // the summaries pulled out. A blank transcript search cannot mean anything.
  if (!q && SEARCH.mode === 'words') {
    toast('Type something to search for'); if (field) field.focus(); return;
  }
  SEARCH.q = q; SEARCH.busy = true; SEARCH.error = ''; SEARCH.results = null;
  document.getElementById('sresults').innerHTML = searchResultsHtml();
  try {
    let url;
    if (SEARCH.mode === 'facts') {
      url = '/api/entities?q=' + encodeURIComponent(q);
    } else {
      url = '/api/transcripts/search?q=' + encodeURIComponent(q);
      if (SEARCH.feed) url += '&feed=' + encodeURIComponent(SEARCH.feed);
    }
    const d = await api(url);
    SEARCH.results = d.results; SEARCH.count = d.count;
  } catch (e) {
    SEARCH.error = e.message;
  }
  SEARCH.busy = false;
  const box = document.getElementById('sresults');
  if (box) box.innerHTML = searchResultsHtml();
}

async function rebuildIndex() {
  try {
    const r = await api('/api/reindex', {method:'POST'});
    toast('Rebuilding — watch it in Activity');
    await refresh(true);
    openJob(r.job_id);
  } catch (e) { toast(e.message); }
}

function renderFeeds() {
  if (!STATE.feeds.length) {
    el.innerHTML = `<div class="empty">No podcasts yet.<br><br>
      <button class="btn primary" onclick="go('add')">Add your first</button></div>`;
    return;
  }
  el.innerHTML = STATE.feeds.map(f => `
    <div class="card" onclick="openFeed('${esc(f.slug)}')" style="cursor:pointer">
      <div class="row">
        <div class="grow">
          <div class="title">${esc(f.title || f.slug)}</div>
          <div class="sub">${f.ready_count}/${f.total_count} episodes ready
            · polled ${f.last_polled_at ? esc(f.last_polled_at.slice(0,10)) : 'never'}</div>
        </div>
        <span class="muted">›</span>
      </div>
    </div>`).join('');
}

function renderAdd() {
  el.innerHTML = `
    <div class="card">
      <div class="sub">This tells <b>skipcast</b> which shows to download and cut.
        It does not subscribe your phone — for that, open the podcast here once
        it is added and use the feed link.</div>
    </div>
    <div class="card">
      <h2>${STATE.search_enabled ? 'Search for a podcast' : 'Add by RSS URL'}</h2>
      <div class="stack">
        ${STATE.search_enabled ? `
        <input type="search" id="q" placeholder="Podcast name…"
               autocapitalize="none" autocorrect="off">
        <button class="btn primary" onclick="doSearch(this)">Search</button>` : ''}
        <div id="results"></div>
      </div>
    </div>
    <div class="card">
      <h2>Add by RSS URL</h2>
      <div class="stack">
        <input type="text" id="url" placeholder="https://…/feed.xml"
               autocapitalize="none" autocorrect="off" inputmode="url">
        <button class="btn" onclick="addUrl(this)">Subscribe</button>
      </div>
    </div>`;
  const q = document.getElementById('q');
  if (q) q.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
}

async function doSearch(btn) {
  const field = document.getElementById('q');
  const q = (field ? field.value : '').trim();
  if (!q) { toast('Type a podcast name first'); if (field) field.focus(); return; }
  const box = document.getElementById('results');
  box.innerHTML = '<div class="empty"><span class="spin"></span> Searching…</div>';
  try {
    const { results } = await api('/api/search?q=' + encodeURIComponent(q));
    if (!results.length) { box.innerHTML = '<div class="empty">Nothing found.</div>'; return; }
    box.innerHTML = results.map((r, i) => `
      <div class="item row">
        ${r.artwork ? `<img class="art" src="${esc(r.artwork)}" alt="">` : '<div class="art"></div>'}
        <div class="grow">
          <div class="title">${esc(r.title)}</div>
          <div class="sub">${esc(r.author)} · ${r.episode_count} episodes</div>
        </div>
        <button class="btn primary" onclick='subscribe(${JSON.stringify(r.feed_url)}, this)'>Add</button>
      </div>`).join('');
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function subscribe(url, btn, label) {
  // The server fetches and parses the feed before answering, which on a show
  // with hundreds of episodes takes several seconds. Without feedback the tap
  // looks like it did nothing, so say something immediately and keep saying it.
  const original = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  toast('Fetching feed…', 15000);
  try {
    const r = await api('/api/feeds', {method:'POST', body:{url}});
    toast(r.already_subscribed ? `Already had ${r.title || r.slug}`
                               : `Added ${r.title || r.slug}`);
    await refresh(true);
    openFeed(r.slug);
  } catch (e) {
    toast(`Could not add it: ${e.message}`, 5000);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = original ?? (label || 'Add'); }
  }
}

// Declared as a function, not a const arrow, so inline onclick attributes
// resolve it regardless of how the browser scopes global lexical bindings.
function addUrl(btn) {
  const field = document.getElementById('url');
  const u = (field ? field.value : '').trim();
  if (!u) { toast('Paste a feed URL first'); if (field) field.focus(); return; }
  if (!/^https?:\/\//i.test(u)) { toast('That needs to start with http:// or https://'); return; }
  subscribe(u, btn, 'Subscribe');
}

async function openFeed(slug) {
  VIEW = {kind:'feed', slug, data:null};
  el.innerHTML = '<div class="empty"><span class="spin"></span></div>';
  try { VIEW.data = await api(`/api/feeds/${slug}/episodes`); }
  catch (e) { toast(e.message); VIEW = null; return render(); }
  render();
}

function renderFeed(v) {
  const f = v.data.feed, eps = v.data.episodes;
  const feedUrl = `${STATE.base_url}/feeds/${f.slug}.xml`;
  el.innerHTML = `
    <span class="back" onclick="VIEW=null;render()">‹ Podcasts</span>
    <div class="card">
      <div class="title wrapline">${esc(f.title || f.slug)}</div>
      <div class="sub" style="margin:8px 0">Subscribe in your podcast app:</div>
      <code>${esc(feedUrl)}</code>
      <div class="wrap" style="margin-top:11px">
        <button class="btn" onclick='copy(${JSON.stringify(feedUrl)})'>Copy link</button>
        <a class="btn" href="${esc(feedUrl)}">Open</a>
      </div>
    </div>
    <div class="card">
      <h2>Fetch new episodes</h2>
      <div class="wrap">
        <button class="btn primary" onclick="pollFeed('${esc(f.slug)}',1)">Newest 1</button>
        <button class="btn" onclick="pollFeed('${esc(f.slug)}',3)">Newest 3</button>
        <button class="btn danger" onclick="unsub('${esc(f.slug)}')">Unsubscribe</button>
      </div>
      <div class="sub" style="margin-top:9px">Each episode takes roughly 15 minutes
        to download, diarize and cut.</div>
    </div>
    ${feedRulesHtml(f)}
    <div class="card">
      <h2>Episodes</h2>
      ${eps.length ? eps.map(e => `
        <div class="item row" onclick="openEpisode('${esc(e.key)}')" style="cursor:pointer">
          <div class="grow">
            <div class="title">${esc(e.title)}</div>
            <div class="sub">${e.status === 'ready'
              ? `${mins(e.result_seconds)} · ${mins(e.cut_seconds)} of ${esc(e.cut_speakers || 'nobody')} removed`
              : esc((e.error || e.status).slice(0,90))}</div>
          </div>
          <span class="pill ${esc(e.status)}">${esc(e.status)}</span>
        </div>`).join('')
      : '<div class="empty">Nothing fetched yet. Tap “Newest 1”.</div>'}
    </div>`;
}

/* Three states, not a toggle: a speaker either follows their global flag or
   this show overrides it in one direction. "Keep" is the one that needs the
   distinction — it means keep them here even though they are cut elsewhere. */
function feedRulesHtml(f) {
  if (!STATE.speakers.length) return '';
  const rules = STATE.feed_rules || [];
  return `
    <div class="card">
      <h2>Who gets cut from this podcast</h2>
      <div class="sub" style="margin-bottom:11px">Default follows the switch on
        the Speakers tab. Set it here to make this show an exception.
        Episodes already fetched need a re-cut.</div>
      ${STATE.speakers.map(s => {
        const rule = rules.find(r => r.slug === f.slug && r.speaker === s.name);
        const mode = rule ? (rule.skip ? 'cut' : 'keep') : 'default';
        const on = m => m === mode ? 'primary' : '';
        const n = JSON.stringify(s.name), slug = JSON.stringify(f.slug);
        return `
          <div class="item">
            <div class="row">
              <div class="grow">
                <div class="title">${esc(s.name)}</div>
                <div class="sub">${mode === 'default'
                  ? (s.skip ? 'cut from every podcast' : 'kept everywhere')
                  : (mode === 'cut' ? 'cut from this show'
                     : 'kept here' + (s.skip ? ', despite being cut elsewhere' : ''))}</div>
              </div>
            </div>
            <div class="wrap" style="margin-top:8px">
              <button class="btn ${on('default')}" onclick='setRule(${slug},${n},null)'>Default</button>
              <button class="btn ${on('cut')}" onclick='setRule(${slug},${n},true)'>Cut</button>
              <button class="btn ${on('keep')}" onclick='setRule(${slug},${n},false)'>Keep</button>
            </div>
          </div>`;
      }).join('')}
    </div>`;
}

async function setRule(slug, name, skip) {
  try {
    await api(`/api/feeds/${slug}/rules`, {method:'POST', body:{name, skip}});
    toast(skip === null ? `${name} follows the global setting here`
          : skip ? `${name} will be cut from this show`
                 : `${name} will be kept on this show`);
    await refresh(true);
    openFeed(slug);
  } catch (e) { toast(e.message); }
}

async function pollFeed(slug, limit) {
  try {
    const r = await api(`/api/feeds/${slug}/poll`, {method:'POST', body:{limit}});
    toast('Started — watch it in Activity');
    await refresh(true);
    openJob(r.job_id);
  } catch (e) { toast(e.message); }
}

async function unsub(slug) {
  if (!confirm('Unsubscribe? Downloaded files stay on disk.')) return;
  try {
    await api(`/api/feeds/${slug}`, {method:'DELETE'});
    VIEW = null; toast('Unsubscribed'); refresh();
  } catch (e) { toast(e.message); }
}

async function openEpisode(key) {
  VIEW = {kind:'episode', key, data:null};
  el.innerHTML = '<div class="empty"><span class="spin"></span></div>';
  try { VIEW.data = await api(`/api/episodes/${key}`); }
  catch (e) { toast(e.message); VIEW = null; return render(); }
  render();
}

function renderEpisode(v) {
  const e = v.data;
  const known = STATE.speakers.map(s => `<option value="${esc(s.name)}">`).join('');
  el.innerHTML = `
    <span class="back" onclick="VIEW=null;render()">‹ Back</span>
    <div class="card">
      <div class="title wrapline">${esc(e.title)}</div>
      <div class="sub" style="margin-top:6px">
        ${e.status === 'ready'
          ? `${mins(e.original_seconds)} → <b>${mins(e.result_seconds)}</b> ·
             ${mins(e.cut_seconds)} of ${esc(e.cut_speakers || 'nobody')} removed`
          : esc(e.error || e.status)}
      </div>
    </div>
    ${e.summary ? `<div class="card">
      <h2>Summary${e.index && e.index.kind_label
        ? ` <span class="muted" style="text-transform:none;letter-spacing:0">·
            ${esc(e.index.kind_label)}</span>` : ''}</h2>
      <div class="md">${md(e.summary)}</div>
      <div class="wrap" style="margin-top:11px">
        ${e.has_transcript ? `<a class="btn"
          href="/api/episodes/${esc(e.key)}/transcript" target="_blank">Read transcript</a>` : ''}
        ${STATE.summary_ready ? `<button class="btn"
          onclick="epJob('${esc(e.key)}','summarize')">Summarise again</button>` : ''}
      </div>
      ${e.index ? '' : `<div class="sub" style="margin-top:9px">This summary
        predates topic links. Summarising again adds them — it reuses the
        transcript, so it takes about a minute.</div>`}
    </div>` : `<div class="card">
      <h2>Summary</h2>
      <div class="sub">Not summarised yet.
        ${e.has_transcript ? 'Transcript is ready.' : ''}</div>
      <button class="btn primary" style="margin-top:11px"
        onclick="epJob('${esc(e.key)}','summarize')"
        ${STATE.summary_ready ? '' : 'disabled'}>Summarise this episode</button>
      <div class="sub" style="margin-top:9px">${
        !STATE.summary_enabled
          ? 'Summaries are switched off in config.toml.'
          : STATE.summary_ready
            ? `Transcribes locally, then summarises with ${esc(STATE.summary_provider)}. Takes about 20 minutes.`
            : `No API key for ${esc(STATE.summary_provider)}. Add it to the .env file beside config.toml, then restart the service — it only reads the file at startup.`
      }</div>
    </div>`}
    ${topicsHtml(e)}
    <div class="card">
      <h2>Who is in this episode</h2>
      <div class="sub" style="margin-bottom:11px">Tap ▶ to hear a voice, then name it.
        Named voices are recognised in every future episode.</div>
      ${e.clusters.length ? e.clusters.map(c => `
        <div class="item">
          <div class="row">
            <div class="swatch" style="background:${esc(c.color)}"></div>
            <div class="grow">
              <div class="title">${esc(c.matched_name || c.speaker_label)}
                ${c.skip ? '<span class="pill" style="color:var(--bad)">cut</span>' : ''}</div>
              <div class="sub">${mins(c.total_seconds)} ·
                ${c.matched_name ? `matched ${c.similarity.toFixed(2)}`
                  : (c.closest_name ? `closest ${esc(c.closest_name)} ${c.similarity.toFixed(2)}`
                                    : 'not recognised')}</div>
            </div>
          </div>
          <div class="wrap" style="margin-top:9px">
            ${c.samples.map(s => `<button class="btn"
                onclick="play(this,'${esc(e.key)}',${s.start},${s.end})">▶ ${clock(s.start)}
                <span class="muted">${s.duration}s</span></button>`).join('')}
          </div>
          ${c.has_embedding ? `
          <div class="row" style="margin-top:9px">
            <input type="text" class="grow" list="known" placeholder="Name this voice…"
                   value="${esc(c.matched_name || '')}"
                   id="n-${esc(c.speaker_label)}" autocapitalize="words">
            <button class="btn primary"
              onclick="nameIt('${esc(e.key)}','${esc(c.speaker_label)}')">Save</button>
          </div>` : '<div class="sub" style="margin-top:8px">Too little clean speech to identify.</div>'}
        </div>`).join('')
      : '<div class="empty">Not diarized yet.</div>'}
      <datalist id="known">${known}</datalist>
    </div>
    <div class="card">
      <h2>Redo</h2>
      <div class="wrap">
        <button class="btn primary" onclick="epJob('${esc(e.key)}','recut')">Re-cut</button>
        <button class="btn" onclick="epJob('${esc(e.key)}','reprocess')">Reprocess</button>
      </div>
      <div class="sub" style="margin-top:9px">Re-cut re-applies the current skip flags
        using the existing analysis — about a minute. Reprocess downloads and
        re-analyses from scratch.</div>
    </div>`;
}

/* Topics and specifics come from the structured half of the summary. Their
   timestamps are the original episode's; the server sends the matching
   position in the edit alongside, which is what the jump uses. */
function topicsHtml(e) {
  const idx = e.index;
  if (!idx || (!idx.topics.length && !idx.specifics.length)) return '';
  const btn = (at, label) => `<button class="btn" style="padding:5px 10px;font-size:13px"
      onclick="event.stopPropagation();playEpisode('${esc(e.key)}',${at})">
      ▶ ${label}</button>`;

  /* A topic and a quote want opposite things when the timestamp was cut.
     Topics open where the host introduces them, and the host is often exactly
     who was removed — so jump to the next surviving moment, which is where
     that topic actually starts for this listener. A specific is a particular
     thing someone said: if it is gone, saying so beats seeking near it, and
     the retained original can still play it. */
  const jumpTopic = t => t.at_cut == null ? ''
    : btn(t.at_cut, clock(t.at_cut));
  const jumpSpecific = s => {
    if (s.at_cut == null) return '';
    if (!s.removed) return btn(s.at_cut, clock(s.at_cut));
    return `<button class="btn" style="padding:5px 10px;font-size:13px"
              onclick="event.stopPropagation();play(this,'${esc(e.key)}',${s.at_seconds},${s.at_seconds + 25})">
              ▶ ${clock(s.at_seconds)} in the original</button>`;
  };
  const topics = idx.topics.length ? `
    <div class="card">
      <h2>Topics</h2>
      ${idx.topics.map(t => `
        <div class="item">
          <div class="row">
            <div class="grow">
              <div class="title wrapline">${esc(t.title)}</div>
              ${t.one_line ? `<div class="sub wrapline">${esc(t.one_line)}</div>` : ''}
            </div>
            ${jumpTopic(t)}
          </div>
        </div>`).join('')}
    </div>` : '';
  const specifics = idx.specifics.length ? `
    <div class="card">
      <h2>Details worth keeping</h2>
      ${idx.specifics.map(s => `
        <div class="item">
          <div class="row">
            <div class="grow">
              <div class="title wrapline">${esc(s.value)}
                <span class="pill">${esc(s.type)}</span>
                ${s.confidence && s.confidence !== 'firm'
                  ? `<span class="pill" style="color:var(--warn)">${esc(s.confidence)}</span>` : ''}
              </div>
              ${s.detail ? `<div class="sub wrapline">${esc(s.detail)}</div>` : ''}
              ${s.speaker ? `<div class="sub">— ${esc(s.speaker)}</div>` : ''}
            </div>
            ${jumpSpecific(s)}
          </div>
        </div>`).join('')}
    </div>` : '';
  return topics + specifics;
}

async function nameIt(key, cluster) {
  const name = document.getElementById('n-' + cluster).value.trim();
  if (!name) return toast('Enter a name first');
  try {
    await api(`/api/episodes/${key}/label`, {method:'POST',
      body:{cluster_label: cluster, name}});
    toast(`Saved ${name}`);
    await refresh(true);
    openEpisode(key);
  } catch (e) { toast(e.message); }
}

async function epJob(key, action) {
  try {
    const r = await api(`/api/episodes/${key}/${action}`, {method:'POST'});
    toast('Started'); await refresh(true); openJob(r.job_id);
  } catch (e) { toast(e.message); }
}

function renderSpeakers() {
  el.innerHTML = `
    <div class="card">
      <h2>Known voices</h2>
      <div class="sub" style="margin-bottom:11px">Switch someone on to cut them from
        every future episode. Already-processed episodes need a re-cut.</div>
      ${STATE.speakers.length ? STATE.speakers.map(s => `
        <div class="item row">
          <div class="grow">
            <div class="title">${esc(s.name)}</div>
            <div class="sub">${s.profiles} voice sample${s.profiles === 1 ? '' : 's'}
              · ${mins(s.total_seconds)} heard</div>
          </div>
          <span class="sub">cut</span>
          <label class="switch">
            <input type="checkbox" ${s.skip ? 'checked' : ''}
                   onchange='setSkip(${JSON.stringify(s.name)}, this.checked)'>
            <span class="slider"></span>
          </label>
        </div>`).join('')
      : `<div class="empty">No voices named yet.<br>Open an episode and name the
         speakers you hear.</div>`}
    </div>`;
}

/* ---- people ------------------------------------------------------------ */
let PEOPLE = null;

async function loadPeople() {
  try { PEOPLE = (await api('/api/persons')).persons; }
  catch (e) { PEOPLE = []; }
  if (TAB === 'people' && !VIEW) render();
}

function renderPeople() {
  if (PEOPLE === null) {
    el.innerHTML = '<div class="empty"><span class="spin"></span></div>';
    loadPeople();
    return;
  }
  const taken = new Set(PEOPLE.map(p => p.speaker));
  const available = STATE.speakers.filter(s => !taken.has(s.name));
  el.innerHTML = `
    <div class="card">
      <div class="sub">A feed of one person: every episode any of your podcasts
        has processed where that voice appears, with everyone else edited out.
        Subscribe to it like any other show.</div>
    </div>
    ${PEOPLE.map(p => {
      const url = `${STATE.base_url}/persons/${p.slug}.xml`;
      return `
      <div class="card">
        <div class="row">
          <div class="grow">
            <div class="title">${esc(p.speaker)}</div>
            <div class="sub">${p.ready_count} appearance${p.ready_count === 1 ? '' : 's'}
              · ${mins(p.total_seconds)} total
              · built ${p.built_at ? esc(p.built_at.slice(0,10)) : 'never'}</div>
          </div>
        </div>
        <code style="display:block;margin-top:10px">${esc(url)}</code>
        <div class="wrap" style="margin-top:11px">
          <button class="btn primary" onclick="buildPerson('${esc(p.slug)}')">Rebuild</button>
          <button class="btn" onclick='copy(${JSON.stringify(url)})'>Copy link</button>
          <button class="btn danger" onclick="removePerson('${esc(p.slug)}')">Remove</button>
        </div>
        ${p.episodes.length ? `<div style="margin-top:6px">${p.episodes.map(e => `
          <div class="item">
            <div class="title wrapline" style="font-size:14.5px">${esc(e.title)}</div>
            <div class="sub">${esc(e.feed_title || '')} · ${mins(e.seconds)} of them</div>
          </div>`).join('')}</div>`
        : `<div class="sub" style="margin-top:9px">Nothing built yet. Rebuild to
           scan every processed episode for this voice.</div>`}
      </div>`;
    }).join('')}
    <div class="card">
      <h2>Follow someone</h2>
      ${available.length ? `
        <div class="sub" style="margin-bottom:11px">Pick a known voice. Building
          scans every episode already processed — no new downloads.</div>
        <div class="stack">
          <select id="pname">${available.map(
            s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join('')}</select>
          <button class="btn primary" onclick="addPerson(this)">Make a feed of them</button>
        </div>`
      : `<div class="sub">${STATE.speakers.length
          ? 'Every known voice already has a feed.'
          : 'No voices named yet. Open an episode and name the speakers you hear.'}</div>`}
    </div>`;
}

async function addPerson(btn) {
  const sel = document.getElementById('pname');
  if (!sel) return;
  btn.disabled = true;
  try {
    const r = await api('/api/persons', {method:'POST', body:{name: sel.value}});
    toast(`Following ${r.speaker}`);
    await loadPeople();
    buildPerson(r.slug);
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; }
}

async function buildPerson(slug) {
  try {
    const r = await api(`/api/persons/${slug}/build`, {method:'POST', body:{}});
    toast('Building — watch it in Activity');
    await refresh(true);
    openJob(r.job_id);
  } catch (e) { toast(e.message); }
}

async function removePerson(slug) {
  if (!confirm('Remove this person feed? The edited audio stays on disk.')) return;
  try {
    await api(`/api/persons/${slug}`, {method:'DELETE'});
    toast('Removed');
    await loadPeople();
    render();
  } catch (e) { toast(e.message); }
}

async function setSkip(name, skip) {
  try {
    await api(`/api/speakers/${encodeURIComponent(name)}/skip`, {method:'POST', body:{skip}});
    toast(skip ? `${name} will be cut` : `${name} will be kept`);
    refresh(true);
  } catch (e) { toast(e.message); }
}

function renderJobs() {
  el.innerHTML = `<div class="card"><h2>Recent activity</h2>
    ${STATE.jobs.length ? STATE.jobs.map(j => `
      <div class="item row" onclick="openJob(${j.id})" style="cursor:pointer">
        <div class="grow">
          <div class="title">${esc(j.label || j.kind)}</div>
          <div class="sub">${esc((j.progress || j.error || '').slice(0,80))}</div>
        </div>
        <span class="pill ${esc(j.status)}">${j.status === 'running'
          ? '<span class="spin"></span> running' : esc(j.status)}</span>
      </div>`).join('')
    : '<div class="empty">Nothing has run yet.</div>'}</div>`;
}

async function openJob(id) {
  VIEW = {kind:'job', id, data:null};
  try { VIEW.data = await api('/api/jobs/' + id); } catch (e) { toast(e.message); }
  render();
}

function renderJob(v) {
  const j = v.data || {};
  el.innerHTML = `
    <span class="back" onclick="VIEW=null;TAB='jobs';syncTabs();render()">‹ Activity</span>
    <div class="card">
      <div class="row">
        <div class="grow"><div class="title">${esc(j.label || j.kind || '')}</div>
        <div class="sub">${esc(j.started_at || j.created_at || '')}</div></div>
        <span class="pill ${esc(j.status)}">${j.status === 'running'
          ? '<span class="spin"></span> running' : esc(j.status)}</span>
      </div>
      ${j.status === 'queued' ? `<button class="btn danger" style="margin-top:11px"
        onclick="cancelJob(${j.id})">Cancel</button>` : ''}
      ${j.error ? `<div class="sub" style="color:var(--bad);margin-top:11px">${esc(j.error)}</div>` : ''}
    </div>
    <div class="card"><h2>Log</h2><pre id="log">${esc(j.log || '(nothing yet)')}</pre></div>`;
  const pre = document.getElementById('log');
  if (pre) pre.scrollTop = pre.scrollHeight;
  if (j.status === 'running' || j.status === 'queued') {
    setTimeout(() => { if (VIEW && VIEW.kind === 'job' && VIEW.id === j.id) openJob(j.id); }, 3000);
  }
}

async function cancelJob(id) {
  const r = await api(`/api/jobs/${id}/cancel`, {method:'POST'});
  toast(r.ok ? 'Cancelled' : r.note); refresh(true); openJob(id);
}

function copy(text) {
  if (navigator.clipboard) navigator.clipboard.writeText(text).then(
    () => toast('Copied'), () => toast('Copy failed'));
  else toast('Copy not available');
}

/* ---- nav -------------------------------------------------------------- */
function syncTabs() {
  document.querySelectorAll('nav button').forEach(
    b => b.classList.toggle('on', b.dataset.tab === TAB));
}
function go(tab) {
  TAB = tab; VIEW = null; syncTabs(); render();
  if (tab === 'listen') loadListen();
  if (tab === 'people') loadPeople();
}
document.querySelectorAll('nav button').forEach(
  b => b.onclick = () => go(b.dataset.tab));

document.getElementById('m-rate').textContent = RATE + '×';
refresh();
</script></body></html>
"""
