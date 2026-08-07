"""The control panel served at /.

One page, vanilla JS, no build step and no CDN. Laid out for a phone first
because that is where it gets used — the desktop is meant to become a box you
never touch.

The shell is three tabs (Home, Library, Activity) with drill-down views routed
through the URL hash, a floating mini-player, and a full-screen now-playing
sheet. Show artwork comes straight from each feed's own image URL; when it
fails to load, a gradient with the show's initials stands in.
"""

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0a0e">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon.svg">
<title>skipcast</title>
<style>
  :root { --bg:#0a0a0e; --card:#16161c; --card2:#1e1e26; --line:#26262e;
          --fg:#f2f3f7; --muted:#9a9fad; --accent:#6f86ff;
          --grad:linear-gradient(135deg,#5b8cff,#8b5cf6);
          --ok:#4ade80; --warn:#fbbf24; --bad:#f87171;
          --chrome:rgba(12,12,17,.85);
          --tabbar-h:calc(56px + env(safe-area-inset-bottom)); }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f4f4f8; --card:#ffffff; --card2:#ebebf1; --line:#dcdce4;
            --fg:#17171d; --muted:#5f6572; --accent:#4f63e0;
            --ok:#15803d; --warn:#b45309; --bad:#dc2626;
            --chrome:rgba(244,244,248,.85); }
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  main { padding:4px 16px 0; max-width:640px; margin:0 auto;
         padding-bottom:calc(var(--tabbar-h) + 20px); }
  body.playing main { padding-bottom:calc(var(--tabbar-h) + 76px); }
  button { font:inherit; border:none; background:none; color:inherit; padding:0;
           cursor:pointer; }
  input, select { font:inherit; }

  /* ---- shared ---- */
  .bigtitle { display:flex; align-items:center; gap:10px;
              padding:calc(14px + env(safe-area-inset-top)) 0 12px; }
  .bigtitle h2 { font-size:30px; letter-spacing:-.03em; margin:0; }
  .bigtitle .spacer { flex:1; }
  .runpill { background:var(--card2); border:1px solid var(--line); color:var(--accent);
             font-size:12px; border-radius:99px; padding:4px 11px; }
  .addbtn { color:var(--accent); font-size:27px; font-weight:400; line-height:1;
            padding:2px 6px; }
  .sec { display:flex; align-items:baseline; justify-content:space-between;
         margin:4px 0 10px; }
  .sec h4 { font-size:18px; letter-spacing:-.02em; margin:0; }
  .sec .hint { color:var(--muted); font-size:12px; }
  .sec a, a.act { color:var(--accent); font-size:13px; text-decoration:none;
                  cursor:pointer; }
  .card { background:var(--card); border-radius:14px; padding:4px 14px;
          margin-bottom:18px; }
  .card.pad { padding:14px; }
  .sub { color:var(--muted); font-size:12.5px; }
  .muted { color:var(--muted); }
  .spin { display:inline-block; width:13px; height:13px; border:2px solid var(--line);
    border-top-color:var(--accent); border-radius:50%; animation:sp .8s linear infinite;
    vertical-align:-2px; }
  @keyframes sp { to { transform:rotate(360deg); } }
  .empty { color:var(--muted); text-align:center; padding:30px 10px; font-size:14px; }
  .toast { position:fixed; left:50%; transform:translateX(-50%);
    bottom:calc(var(--tabbar-h) + 66px);
    background:var(--fg); color:var(--bg); padding:11px 17px; border-radius:12px;
    font-size:14px; z-index:200; max-width:88%; }
  code { background:var(--card2); padding:2px 6px; border-radius:6px; font-size:12.5px;
         word-break:break-all; }
  pre { background:var(--card2); border-radius:10px; padding:11px; overflow-x:auto;
        font-size:11.5px; line-height:1.45; max-height:300px; overflow-y:auto;
        margin:0 0 12px; white-space:pre-wrap; word-break:break-word; }
  .pill { font-size:11px; padding:2px 8px; border-radius:99px; background:var(--card2);
    color:var(--muted); border:1px solid var(--line); white-space:nowrap; }
  .pill.ready { color:var(--ok); } .pill.failed, .pill.refused { color:var(--bad); }
  .pill.running { color:var(--accent); } .pill.pending, .pill.queued { color:var(--warn); }
  .btn { display:inline-block; background:var(--card2); color:var(--fg);
         border:1px solid var(--line); border-radius:99px; padding:9px 15px;
         font-size:13.5px; font-weight:600; text-decoration:none; }
  .btn:active { transform:scale(.97); }
  .btn.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  .btn.danger { color:var(--bad); }
  .btn:disabled { opacity:.45; }
  .wrap { display:flex; flex-wrap:wrap; gap:8px; }
  .stack { display:flex; flex-direction:column; gap:9px; }
  .row { display:flex; align-items:center; gap:10px; }
  .grow { flex:1; min-width:0; }
  input[type=text], input[type=search], select { width:100%; background:var(--card2);
    color:var(--fg); border:1px solid var(--line); border-radius:10px; padding:11px;
    font-size:16px; }
  select { appearance:none; -webkit-appearance:none; }
  .md { font-size:14.5px; line-height:1.6; }
  .md h3 { font-size:15px; margin:16px 0 6px; }
  .md h4 { font-size:14px; margin:14px 0 5px; color:var(--accent); }
  .md ul { margin:6px 0; padding-left:20px; }
  .md li { margin-bottom:5px; }
  .backrow { display:flex; justify-content:space-between; align-items:center;
             padding:calc(12px + env(safe-area-inset-top)) 0 10px; }
  .backrow .b { color:var(--accent); font-size:15px; cursor:pointer; }
  .backrow .dots { color:var(--muted); font-size:22px; letter-spacing:2px;
                   padding:0 4px; cursor:pointer; }

  /* ---- artwork ---- */
  .art { position:relative; border-radius:10px; overflow:hidden; flex:none;
         background:var(--grad); }
  .art img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }
  .art .ini { position:absolute; inset:0; display:flex; align-items:center;
              justify-content:center; font-weight:700; color:#fff; opacity:.92; }

  /* ---- search bar ---- */
  .searchrow { display:flex; align-items:center; gap:10px; margin-bottom:18px; }
  .search { flex:1; display:flex; align-items:center; gap:8px; background:var(--card2);
            border-radius:12px; padding:0 12px; }
  .search svg { flex:none; }
  .search input { background:none; border:none; padding:11px 0; outline:none;
                  color:var(--fg); flex:1; min-width:0; }
  .cancel { color:var(--accent); font-size:14.5px; }
  .chiprow { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }
  .chip { display:inline-flex; align-items:center; gap:6px; background:var(--card2);
          border:1px solid var(--line); border-radius:99px; padding:6px 12px;
          font-size:12.5px; color:var(--fg); cursor:pointer; }
  .chip.watch { color:var(--accent); border-color:rgba(111,134,255,.4); }
  .chip .n { color:var(--ok); font-weight:700; }
  .chip .x { color:var(--muted); padding:6px 4px 6px 8px; margin:-6px -6px -6px -2px; }
  .hit { padding:11px 0; border-bottom:1px solid var(--line); }
  .hit:last-child { border-bottom:none; }
  .hit .q { font-size:14px; line-height:1.45; }
  .hit .q mark { background:rgba(111,134,255,.28); color:inherit; border-radius:3px;
                 padding:0 2px; }
  .hit .m { font-size:12px; color:var(--muted); margin-top:4px; }
  /* Tappable inline links: the visual stays small, the target does not. */
  .hit .m .at, .chapter .at { color:var(--accent); cursor:pointer; font-weight:600;
    display:inline-block; padding:8px 6px; margin:-8px -2px; }

  /* ---- home ---- */
  .carousel { display:flex; gap:12px; overflow-x:auto; margin:0 -16px 20px;
              padding:0 16px; scrollbar-width:none;
              scroll-snap-type:x mandatory; scroll-padding-left:16px; }
  .carousel::-webkit-scrollbar { display:none; }
  .ccard { width:148px; flex:none; cursor:pointer; scroll-snap-align:start; }
  .ccard .art { width:148px; height:148px; border-radius:12px; }
  .ccard .play { position:absolute; right:8px; bottom:8px; width:34px; height:34px;
                 border-radius:50%; background:rgba(20,20,26,.82); color:#fff;
                 display:flex; align-items:center; justify-content:center;
                 font-size:13px; }
  .ccard .t { font-size:13px; font-weight:600; line-height:1.3; margin-top:8px;
              display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
              overflow:hidden; }
  .ccard .s { font-size:12px; color:var(--muted); margin-top:2px; }
  .prog { height:3px; background:var(--card2); border-radius:2px; margin-top:7px;
          overflow:hidden; }
  .prog i { display:block; height:100%; background:var(--accent); }
  /* The hero keeps a gradient but a deep, quiet one; everything you read at
     length — the builder and each digest's rundown — sits on a normal card. */
  .digest { background:linear-gradient(135deg,#323d7d,#43306e); border-radius:14px;
            padding:14px; margin-bottom:12px; color:#fff; }
  .digest .row { cursor:pointer; }
  .digest .ic { width:44px; height:44px; border-radius:10px;
                background:rgba(255,255,255,.14); display:flex; align-items:center;
                justify-content:center; font-size:20px; flex:none; }
  .digest .t { font-size:14.5px; font-weight:650; }
  .digest .s { font-size:12.5px; color:rgba(255,255,255,.72); }
  .digest .go { background:rgba(255,255,255,.92); color:#1d1d29; font-weight:700;
                border-radius:99px; padding:9px 15px; font-size:13px; flex:none; }
  .rundown .chapter .t { font-size:13px; }
  .rundown .chapter .why { display:block; color:var(--muted); font-weight:400;
                           font-size:11.5px; margin-top:2px; line-height:1.4; }
  .rundown .take { padding-left:35px; }
  .rundown .len { flex:none; color:var(--muted); font-size:12px;
                  font-variant-numeric:tabular-nums; }
  .shelf { display:flex; gap:14px; overflow-x:auto; margin:0 -16px 20px;
           padding:0 16px; scrollbar-width:none; }
  .shelf::-webkit-scrollbar { display:none; }
  .shelf .show { width:78px; flex:none; text-align:center; cursor:pointer; }
  .shelf .art { width:78px; height:78px; border-radius:14px; }
  .shelf .art.person { border-radius:50%; }
  .shelf .n { font-size:11px; color:var(--muted); margin-top:6px; line-height:1.25;
              display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
              overflow:hidden; }
  .shelf .new { position:absolute; top:-4px; right:-4px; background:var(--accent);
                color:#fff; font-size:10px; font-weight:700; border-radius:99px;
                padding:1px 6px; }
  .eprow { display:flex; gap:12px; padding:11px 0; border-bottom:1px solid var(--line);
           align-items:center; cursor:pointer; }
  .eprow:last-child { border-bottom:none; }
  .eprow .art { width:52px; height:52px; }
  .eprow .t { font-size:14px; font-weight:600; line-height:1.3;
              display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
              overflow:hidden; }
  .eprow .s { font-size:12px; color:var(--muted); margin-top:3px; }
  .pbtn { flex:none; display:flex; align-items:center; gap:6px; background:var(--card2);
          border-radius:99px; padding:7px 12px; font-size:12.5px; font-weight:650;
          color:var(--fg); border:1px solid var(--line); }
  .pbtn.on { background:var(--accent); border-color:var(--accent); color:#fff; }
  h2.fold { font-size:15px; font-weight:600; margin:0; padding:13px 2px;
            border-top:1px solid var(--line); display:flex; align-items:center;
            justify-content:space-between; cursor:pointer; }
  h2.fold:first-child { border-top:none; }
  h2.fold .s { display:block; color:var(--muted); font-size:12.5px; font-weight:400;
               margin-top:2px; }
  h2.fold .ch { color:var(--muted); font-weight:400; }
  .foldbody { padding:2px 2px 14px; }

  /* ---- show page ---- */
  .showhero { text-align:center; padding:4px 0 14px; }
  .showhero .art { width:150px; height:150px; border-radius:18px; margin:0 auto;
                   box-shadow:0 16px 40px rgba(0,0,0,.45); }
  .showhero h5 { font-size:20px; letter-spacing:-.02em; margin:14px 0 0;
                 line-height:1.25; }
  .showhero .a { color:var(--muted); font-size:13px; margin-top:3px; }
  .showhero .k { display:inline-block; margin-top:10px; background:var(--card2);
                 border:1px solid var(--line); color:var(--muted); font-size:12px;
                 border-radius:99px; padding:5px 12px; }
  .showhero .k b { color:var(--fg); }
  .cta { display:flex; gap:10px; justify-content:center; margin:14px 0 18px; }

  /* ---- episode page ---- */
  .ephero { display:flex; gap:14px; margin:6px 0 4px; }
  .ephero .art { width:92px; height:92px; border-radius:14px; }
  .ephero h5 { font-size:16.5px; line-height:1.3; letter-spacing:-.01em; margin:0; }
  .ephero .m { color:var(--muted); font-size:12.5px; margin-top:6px; }
  .bigplay { display:flex; align-items:center; justify-content:center; gap:8px;
             background:var(--accent); color:#fff; font-weight:700; font-size:15px;
             border-radius:14px; padding:13px; margin:16px 0 6px; width:100%; }
  .savings { text-align:center; color:var(--muted); font-size:12px;
             margin-bottom:16px; }
  .chapter { display:flex; align-items:center; gap:11px; padding:10px 0;
             border-bottom:1px solid var(--line); }
  .chapter:last-child { border-bottom:none; }
  .chapter .no { width:24px; height:24px; border-radius:8px; background:var(--card2);
                 color:var(--muted); font-size:11px; font-weight:700; display:flex;
                 align-items:center; justify-content:center; flex:none; }
  .chapter .t { font-size:13.5px; font-weight:600; line-height:1.35; flex:1;
                min-width:0; }
  .chapter .t .rel { display:block; color:var(--warn); font-size:11.5px;
                     font-weight:400; margin-top:2px; }
  .chapter .at { font-size:12.5px; flex:none; padding:10px 2px 10px 12px;
                 margin:-10px 0; }
  .kchips { display:flex; gap:8px; overflow-x:auto; margin:0 -16px 8px;
            padding:0 16px; scrollbar-width:none;
            scroll-snap-type:x mandatory; scroll-padding-left:16px; }
  .kchips::-webkit-scrollbar { display:none; }
  .kchip { flex:none; background:var(--card); border:1px solid var(--line);
           border-radius:12px; padding:9px 12px; max-width:210px; cursor:pointer;
           scroll-snap-align:start; }
  .kchip .v { font-size:12.5px; font-weight:650; white-space:nowrap; overflow:hidden;
              text-overflow:ellipsis; }
  .kchip .d { font-size:11px; color:var(--muted); margin-top:2px; white-space:nowrap;
              overflow:hidden; text-overflow:ellipsis; }
  .swatch { width:10px; height:34px; border-radius:3px; flex:none; }
  .item { padding:11px 0; border-bottom:1px solid var(--line); }
  .item:last-child { border-bottom:none; }
  .title { font-weight:600; }
  .wrapline { white-space:normal; }
  .switch { position:relative; width:48px; height:29px; flex:none; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:var(--card2);
            border:1px solid var(--line); border-radius:99px; transition:.15s; }
  .slider:before { content:""; position:absolute; height:21px; width:21px; left:3px;
    bottom:3px; background:var(--fg); border-radius:50%; transition:.15s; }
  .switch input:checked + .slider { background:var(--bad); border-color:var(--bad); }
  .switch input:checked + .slider:before { transform:translateX(19px); background:#fff; }

  /* ---- library ---- */
  .seg { display:flex; background:var(--card2); border-radius:10px; padding:3px;
         margin-bottom:18px; }
  .seg button { flex:1; text-align:center; padding:7px; border-radius:8px;
                font-size:13.5px; color:var(--muted); }
  .seg button.on { background:var(--card); color:var(--fg); font-weight:650;
                   box-shadow:0 1px 4px rgba(0,0,0,.25); }
  .lgrid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:20px; }
  .lcell { cursor:pointer; }
  .lcell .art { width:100%; aspect-ratio:1; border-radius:14px; }
  .lcell .n { font-size:13px; font-weight:600; margin-top:7px; line-height:1.3; }
  .lcell .s { font-size:11.5px; color:var(--muted); margin-top:1px; }
  .lcell .new { position:absolute; top:8px; right:8px; background:var(--accent);
                color:#fff; font-size:11px; font-weight:700; border-radius:99px;
                padding:2px 8px; }
  .prow { padding:11px 0; border-bottom:1px solid var(--line); }
  .prow:last-child { border-bottom:none; }
  .prow .head { display:flex; align-items:center; gap:12px; cursor:pointer; }
  .pava { width:42px; height:42px; border-radius:50%; background:var(--grad);
          flex:none; display:flex; align-items:center; justify-content:center;
          font-weight:700; font-size:14px; color:#fff; }
  .prow .t { font-size:14px; font-weight:600; }
  .prow .s { font-size:12px; color:var(--muted); margin-top:2px; }
  .cutpill { margin-left:auto; flex:none; font-size:11px; border-radius:99px;
             padding:4px 10px; background:rgba(239,68,68,.14); color:var(--bad);
             font-weight:650; }
  .keeppill { margin-left:auto; flex:none; font-size:11px; border-radius:99px;
              padding:4px 10px; background:var(--card2); color:var(--muted);
              font-weight:650; }
  .pbody { padding:12px 0 6px 54px; }
  img.podart { width:56px; height:56px; border-radius:9px; flex:none;
               background:var(--card2); }

  /* ---- bottom chrome: the mini player docks flush on the tab bar, and the
     two read as one fixed unit — no gap, no content bleeding through. ---- */
  #mini { position:fixed; left:0; right:0; bottom:var(--tabbar-h); z-index:60;
          background:var(--chrome); backdrop-filter:blur(16px);
          -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--line);
          display:none; align-items:center; gap:10px; padding:9px 14px 11px;
          cursor:pointer; }
  #mini.on { display:flex; }
  #mini .art { width:34px; height:34px; border-radius:8px; }
  #mini .t { font-size:12.5px; font-weight:600; flex:1; min-width:0;
             white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #mini .mp { position:absolute; left:0; right:0; bottom:0; height:2px; }
  #mini .mp i { display:block; height:100%; width:0; background:var(--accent); }
  #m-play { font-size:17px; padding:6px 10px; }

  #tabbar { position:fixed; left:0; right:0; bottom:0; z-index:50; display:flex;
            border-top:1px solid var(--line); background:var(--chrome);
            backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
            height:var(--tabbar-h);
            padding:6px 0 calc(6px + env(safe-area-inset-bottom)); }
  body.playing #tabbar { border-top:none; }
  #tabbar button { flex:1; display:flex; flex-direction:column;
                   align-items:center; justify-content:center; gap:3px;
                   color:var(--muted); font-size:10.5px; padding:0; }
  #tabbar svg { display:block; stroke:currentColor; }
  #tabbar button.on { color:var(--fg); }
  #tabbar button.on svg { stroke:var(--accent); }
  #tabbar .ic { position:relative; display:block; height:22px; }
  #tabbar .bdg { position:absolute; top:-3px; right:-13px; background:var(--accent);
                 color:#fff; border-radius:99px; font-size:9px; font-weight:700;
                 padding:1px 5px; display:none; }

  /* ---- top chrome: a compact blurred bar that appears once the big title
     has scrolled away, and keeps content out of the status bar. ---- */
  #topbar { position:fixed; top:0; left:0; right:0; z-index:40;
            padding-top:env(safe-area-inset-top); height:calc(40px + env(safe-area-inset-top));
            display:flex; align-items:center; justify-content:center;
            background:var(--chrome); backdrop-filter:blur(16px);
            -webkit-backdrop-filter:blur(16px); border-bottom:1px solid var(--line);
            font-size:14.5px; font-weight:650; opacity:0; pointer-events:none;
            transition:opacity .15s; }
  #topbar.on { opacity:1; }
  #topbar span { max-width:70%; white-space:nowrap; overflow:hidden;
                 text-overflow:ellipsis; }

  /* ---- now playing sheet ---- */
  #np { position:fixed; inset:0; z-index:100; display:flex; flex-direction:column;
        background:linear-gradient(180deg,#2b2f55 0%,#1a1630 55%,#0d0b16 100%);
        color:#fff; transform:translateY(105%); transition:transform .28s ease;
        padding:calc(8px + env(safe-area-inset-top)) 0
                calc(16px + env(safe-area-inset-bottom)); }
  #np.on { transform:translateY(0); }
  #np .grab { width:38px; height:5px; border-radius:3px;
              background:rgba(255,255,255,.28); margin:6px auto 4px; flex:none;
              cursor:pointer; padding:0; }
  #np .from { text-align:center; color:rgba(255,255,255,.55); font-size:11.5px;
              text-transform:uppercase; letter-spacing:.08em; margin-bottom:12px; }
  #np .art { width:min(272px, 68vw); aspect-ratio:1; border-radius:18px;
             margin:0 auto; box-shadow:0 24px 60px rgba(0,0,0,.6); }
  #np .meta { padding:20px 30px 0; }
  #np h5 { font-size:18px; line-height:1.3; letter-spacing:-.01em; margin:0;
           display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
           overflow:hidden; }
  #np .sh { color:rgba(255,255,255,.6); font-size:14px; margin-top:3px; }
  #np .chapnow { margin:12px 30px 0; background:rgba(255,255,255,.1);
                 border-radius:12px; padding:9px 13px; font-size:12.5px;
                 display:none; gap:8px; align-items:baseline; }
  #np .chapnow.on { display:flex; }
  #np .chapnow .lbl { color:rgba(255,255,255,.55); flex:none; }
  #np .chapnow b { font-weight:650; }
  #np .scrubbox { margin:16px 30px 0; }
  #scrub { width:100%; -webkit-appearance:none; appearance:none; height:20px;
           background:transparent; display:block; }
  #scrub::-webkit-slider-runnable-track { height:4px;
      background:rgba(255,255,255,.22); border-radius:2px; }
  #scrub::-webkit-slider-thumb { -webkit-appearance:none; width:15px; height:15px;
      border-radius:50%; background:#fff; margin-top:-5.5px; }
  #scrub::-moz-range-track { height:4px; background:rgba(255,255,255,.22);
      border-radius:2px; }
  #scrub::-moz-range-thumb { width:15px; height:15px; border:none;
      border-radius:50%; background:#fff; }
  #np .times { display:flex; justify-content:space-between;
               color:rgba(255,255,255,.55); font-size:11.5px; margin-top:4px;
               font-variant-numeric:tabular-nums; }
  #np .ctrl { display:flex; align-items:center; justify-content:center; gap:34px;
              margin-top:14px; position:relative; }
  #np .sk { color:#fff; font-size:12px; font-weight:650; text-align:center;
            line-height:1.15; opacity:.9; }
  #np .sk b { font-size:15px; display:block; }
  #np .pp { width:66px; height:66px; border-radius:50%; background:#fff;
            color:#14141c; display:flex; align-items:center; justify-content:center;
            font-size:24px; }
  #np .rate { position:absolute; right:30px; color:rgba(255,255,255,.75);
              font-size:13px; font-weight:700; padding:8px; }
  #np .cut { text-align:center; color:rgba(255,255,255,.5); font-size:11.5px;
             margin-top:14px; padding:0 30px; }
  #np .next { margin:auto 18px 4px; background:rgba(0,0,0,.3); border-radius:12px;
              padding:10px 14px; display:none; gap:10px; align-items:baseline;
              font-size:12.5px; color:rgba(255,255,255,.85); cursor:pointer; }
  #np .next.on { display:flex; }
  #np .next .lbl { color:rgba(255,255,255,.45); flex:none; }
  /* Mirrors .rate on the other side of the transport. */
  #np .clip { position:absolute; left:30px; color:rgba(255,255,255,.75);
              font-size:19px; padding:8px; line-height:1; }
  #np .clip:disabled { opacity:.25; }
  #np .clip.saved { color:var(--accent); }

  /* ---- clips ---- */
  .clipcard { padding:14px 15px; border-bottom:1px solid var(--line); }
  .clipcard:last-child { border-bottom:0; }
  /* Forty seconds of speech is a lot of words. Clamped by default so the tab
     reads as a list of moments rather than a wall of transcript. */
  .clipcard .q { font-size:14.5px; line-height:1.5; white-space:pre-wrap;
                 display:-webkit-box; -webkit-line-clamp:5; line-clamp:5;
                 -webkit-box-orient:vertical; overflow:hidden; }
  .clipcard .q.open { display:block; overflow:visible; }
  .clipcard .q.none { color:var(--muted); font-style:italic; }
  .clipcard .more { color:var(--accent); font-size:12.5px; margin-top:6px; }
  .clipcard .cite { color:var(--muted); font-size:12px; margin-top:8px;
                    display:flex; gap:6px; flex-wrap:wrap; align-items:baseline; }
  .clipcard .cite b { color:var(--fg); font-weight:650; }
  .clipcard .acts { display:flex; gap:8px; margin-top:11px; flex-wrap:wrap; }
  .clipcard .acts .btn { padding:7px 12px; font-size:12.5px; }
  .clipcard .note { margin-top:9px; font-size:12.5px; color:var(--warn); }
  .trimbox { margin-top:12px; border-top:1px solid var(--line); padding-top:11px; }
  .trimbox .hint { color:var(--muted); font-size:12px; margin-bottom:9px; }
  .sent { display:block; width:100%; text-align:left; padding:8px 10px;
          border-radius:9px; font-size:13.5px; line-height:1.45;
          color:var(--muted); border:1px solid transparent; }
  .sent.on { background:var(--card2); color:var(--fg); border-color:var(--accent); }
  .sent .who { color:var(--accent); font-size:11px; font-weight:650;
               display:block; margin-bottom:2px; }

  /* ---- action sheet ---- */
  #sheet { position:fixed; inset:0; z-index:150; display:none; }
  #sheet.on { display:block; }
  #sheet .bg { position:absolute; inset:0; background:rgba(0,0,0,.5); }
  #sheet .box { position:absolute; left:10px; right:10px;
                bottom:calc(10px + env(safe-area-inset-bottom));
                background:var(--card); border-radius:16px; overflow:hidden; }
  /* Direct children only: these are the menu's own rows. Buttons nested
     inside an ask sheet's body are ordinary buttons and must stay that way. */
  #sheet .box > button { display:block; width:100%; padding:15px; text-align:center;
                         font-size:15.5px; border-top:1px solid var(--line);
                         color:var(--accent); }
  #sheet .box > button:first-child { border-top:none; }
  #sheet .box > button.danger { color:var(--bad); }
  /* A sheet that asks rather than lists: a heading, what would happen, and
     two ways out. Used for anything that removes audio. */
  #sheet .ask { padding:18px 16px 14px; }
  #sheet .ask h6 { font-size:17px; letter-spacing:-.01em; margin:0 0 4px; }
  #sheet .ask .scrolly { max-height:44vh; overflow-y:auto; margin-top:6px; }
  #sheet .ask .actions { display:flex; gap:10px; margin-top:14px; }
  #sheet .ask .actions .btn { flex:1; text-align:center; padding:12px; }

  /* three-state term pill */
  .tri { display:flex; background:var(--card2); border:1px solid var(--line);
         border-radius:99px; padding:2px; flex:none; }
  .tri button { font-size:11px; font-weight:650; padding:5px 11px;
                border-radius:99px; color:var(--muted); }
  .tri button.w.on { background:rgba(74,222,128,.16); color:var(--ok); }
  .tri button.k.on { background:rgba(248,113,113,.16); color:var(--bad); }
  .suggest { background:var(--card); border:1px solid rgba(111,134,255,.35);
             border-radius:14px; padding:14px; margin-bottom:16px; }
  .suggest .tag { font-size:10.5px; font-weight:700; letter-spacing:.06em;
                  text-transform:uppercase; color:var(--accent); margin-bottom:6px; }
  .warncard { background:var(--card); border:1px solid rgba(251,191,36,.35);
              border-radius:14px; padding:14px; margin-bottom:16px; }
  /* A chapter removed for its subject: listed, struck through, checkable.
     Only the title is struck — text-decoration propagates to descendants and
     cannot be cancelled on them, so the reason line has to sit outside it. */
  .chapter.ghost .t { color:var(--muted); }
  .chapter.ghost .ttl { text-decoration:line-through;
                        text-decoration-color:rgba(154,161,173,.55); }
  .chapter.ghost .no { opacity:.45; }
  .chapter .why { display:block; text-decoration:none; font-size:11.5px;
                  font-weight:400; color:var(--muted); margin-top:2px; }
  .chapter .why b { color:var(--bad); font-weight:600; }
</style></head>
<body>
<div id="topbar"><span id="topbar-t"></span></div>
<main id="main"></main>
<audio id="player" preload="metadata"></audio>

<div id="mini" onclick="openNP()">
  <div class="art"><span class="ini" style="font-size:13px">◍</span>
    <img id="m-art" alt="" style="display:none"></div>
  <div class="t" id="m-title"></div>
  <button id="m-play" onclick="event.stopPropagation();togglePlay()">▶</button>
  <div class="mp"><i id="m-bar"></i></div>
</div>

<div id="np">
  <button class="grab" onclick="closeNP()" aria-label="close player"></button>
  <div class="from" id="np-from"></div>
  <div class="art"><span class="ini" style="font-size:44px">◍</span>
    <img id="np-art" alt="" style="display:none"></div>
  <div class="meta"><h5 id="np-title"></h5><div class="sh" id="np-show"></div></div>
  <div class="chapnow" id="np-chap"><span class="lbl" id="np-chap-n"></span>
    <b id="np-chap-t"></b></div>
  <div class="scrubbox">
    <input type="range" id="scrub" min="0" max="1000" value="0">
    <div class="times"><span id="np-cur">0:00</span><span id="np-left">−0:00</span></div>
  </div>
  <div class="ctrl">
    <button class="clip" id="np-clip" onclick="saveClip()"
            aria-label="save the moment just played" title="Save this moment">✂</button>
    <button class="sk" onclick="seekBy(-15)" aria-label="back 15 seconds"><b>↺</b>15</button>
    <button class="pp" id="np-play" onclick="togglePlay()">▶</button>
    <button class="sk" onclick="seekBy(30)" aria-label="forward 30 seconds"><b>↻</b>30</button>
    <button class="rate" id="np-rate" onclick="cycleRate()">1×</button>
  </div>
  <div class="cut" id="np-cut"></div>
  <div class="next" id="np-next"><span class="lbl">Next chapter</span>
    <span id="np-next-t"></span></div>
</div>

<div id="sheet"><div class="bg" onclick="closeSheet()"></div><div class="box" id="sheetbox"></div></div>

<nav id="tabbar">
  <button data-tab="home"><span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7h-6v7H4a1 1 0 0 1-1-1Z"/></svg></span>Today</button>
  <button data-tab="library"><span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke-width="2"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg></span>Library</button>
  <button data-tab="clips"><span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1Z"/></svg></span>Clips</button>
  <button data-tab="jobs"><span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M4 12h3l2.5-7 5 14 2.5-7h3"/></svg><span class="bdg" id="jobbadge"></span></span>Activity</button>
</nav>

<script>
let STATE = null, TAB = 'home', VIEW = null, TIMER = null;
/* Disclosure and segment state. Reset per-view where noted so a different
   episode or show starts folded again. */
const UI = {digestOpen: false, playedOpen: false, summaryOpen: false,
    adsOpen: false, voicesOpen: false, clustersAll: false, subOpen: false,
    rulesOpen: false, rulesAll: false, aboutOpen: false, detailsAll: false,
    libSeg: 'shows', personOpen: null, dismissedSegments: []};
let HIST = 0;      // in-app hash navigations this session; gates goBack()
let LASTRUN = 0;   // running jobs at last poll; a drop means something finished
const el = document.getElementById('main');
const audio = document.getElementById('player');
let stopAt = null, playingBtn = null;

/* ---- helpers ---------------------------------------------------------- */
const esc = s => (s ?? '').toString().replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mins = s => s ? (s >= 3600 ? `${Math.floor(s/3600)}h ${Math.round(s%3600/60)}m`
                                 : `${Math.round(s/60)}m`) : '0m';
const dateShort = ts => ts ? new Date(ts * 1000)
  .toLocaleDateString(undefined, {day: 'numeric', month: 'short'}) : '';
const clock = t => { const h=Math.floor(t/3600), m=Math.floor(t%3600/60), s=Math.floor(t%60);
  return (h? h+':'+String(m).padStart(2,'0') : String(m))+':'+String(s).padStart(2,'0'); };

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

/* What came out of an episode, named honestly. Two different promises — a
   voice you asked to lose, and material that was never the show — and either
   can be absent, so neither may be assumed when phrasing it. */
function removedLabel(e) {
  const ads = e.interstitial_seconds || 0;
  const topics = e.topic_seconds || 0;
  // Whatever is left over after the two attributed kinds is the speaker cut.
  // Subtracting both matters: without it a skipped chapter gets reported as
  // more of the speaker you asked to lose, which is a different promise.
  const speech = (e.cut_seconds || 0) - ads - topics;
  const bits = [];
  if (speech > 30) bits.push(`${mins(speech)} of ${esc(e.cut_speakers || 'flagged speakers')}`);
  if (topics > 30) bits.push(`${mins(topics)} of ${esc(e.cut_topics || 'skipped topics')}`);
  if (ads > 30) bits.push(`${mins(ads)} of ads`);
  if (!bits.length) return '';
  const last = bits.pop();
  return (bits.length ? bits.join(', ') + ' and ' : '') + last + ' removed';
}

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

function copy(text) {
  if (navigator.clipboard) navigator.clipboard.writeText(text).then(
    () => toast('Copied'), () => toast('Copy failed'));
  else toast('Copy not available');
}

/* Cover art with a gradient-and-initials stand-in behind it: the image is
   hotlinked from the show's own CDN and may fail offline. The initials come
   first in the DOM so a loaded image paints over them. */
function artHtml(url, name, extra = '', fontSize = 0) {
  const ini = (name || '?').split(/\s+/).slice(0, 2).map(w => w[0] || '')
    .join('').toUpperCase();
  return `<div class="art ${extra}">
    <span class="ini"${fontSize ? ` style="font-size:${fontSize}px"` : ''}>${esc(ini)}</span>
    ${url ? `<img src="${esc(url)}" alt="" loading="lazy"
              onerror="this.remove()">` : ''}
  </div>`;
}

function feedArt(slug) {
  const f = (STATE && STATE.feeds || []).find(x => x.slug === slug);
  return f ? f.image_url : null;
}
function feedTitle(slug) {
  const f = (STATE && STATE.feeds || []).find(x => x.slug === slug);
  return f ? (f.title || f.slug) : slug;
}

/* ---- action sheet ------------------------------------------------------ */
let SHEET_ACTIONS = [];
function openSheet(items) {
  SHEET_ACTIONS = items;
  document.getElementById('sheetbox').innerHTML = items.map((it, i) =>
    `<button class="${it.danger ? 'danger' : ''}"
       onclick="runSheet(${i})">${esc(it.label)}</button>`).join('')
    + '<button onclick="closeSheet()" style="color:var(--muted)">Cancel</button>';
  document.getElementById('sheet').classList.add('on');
}
function runSheet(i) { closeSheet(); const a = SHEET_ACTIONS[i]; if (a) a.fn(); }
function closeSheet() { document.getElementById('sheet').classList.remove('on'); }

/* Ask before doing something that removes audio. `body` is already-built HTML
   describing what would go; `confirm` is the one button that does it. */
function askSheet(title, sub, body, confirmLabel, fn) {
  SHEET_ACTIONS = [{fn}];
  document.getElementById('sheetbox').innerHTML = `
    <div class="ask">
      <h6>${title}</h6>
      <div class="sub">${sub}</div>
      <div class="scrolly">${body}</div>
      <div class="actions">
        <button class="btn" onclick="closeSheet()">Cancel</button>
        <button class="btn primary" onclick="runSheet(0)">${esc(confirmLabel)}</button>
      </div>
    </div>`;
  document.getElementById('sheet').classList.add('on');
}

/* ---- player ------------------------------------------------------------ */
/* NOW is either a Listen-list episode or a digest pseudo-episode
   ({digest:true}). Digests have no saved position and no server clock
   conversion — they are one flat file. */
let NOW = null, RATE = 1, scrubbing = false, lastSave = 0;
let NPCH = null;   // chapters for the now-playing sheet, [{title, at}]
const RATES = [1, 1.25, 1.5, 1.75, 2];

/* Speaker samples and short checks share the one <audio>. A sample sets
   stopAt so it cuts off after a few seconds; an episode clears it. */
function play(btn, key, start, end) {
  if (playingBtn) playingBtn.classList.remove('on');
  if (playingBtn === btn && !audio.paused) { audio.pause(); playingBtn = null; return; }
  savePosition(true);
  NOW = null; NPCH = null; syncMini();
  const src = `/source/${key}.mp3`;
  if (!audio.src.endsWith(src)) audio.src = src;
  const go = () => { audio.currentTime = start; stopAt = end; audio.play().catch(()=>{}); };
  if (audio.readyState > 0) go();
  else audio.addEventListener('loadedmetadata', go, {once:true});
  playingBtn = btn; btn.classList.add('on');
}

/* `at` jumps to a position instead of resuming — a search hit or a chapter.
   It is already in the edited file's clock; the server does that conversion,
   because only it knows what was cut. */
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
  NOW = ep; NPCH = null;
  stopAt = null;
  if (playingBtn) { playingBtn.classList.remove('on'); playingBtn = null; }
  audio.src = `/audio/${key}.mp3`;
  audio.playbackRate = RATE;
  const start = at != null ? at : (ep.finished ? 0 : (ep.position || 0));
  const go = () => {
    if (start > 0 && start < (audio.duration || Infinity) - 5) audio.currentTime = start;
    audio.play().catch(e => toast('Playback blocked — tap play again'));
  };
  if (audio.readyState > 0) go();
  else audio.addEventListener('loadedmetadata', go, {once:true});
  syncMini();
  setMediaSession(ep);
  updateMini();
  render();
}

function playDigest(key) {
  const d = (DIGESTS || []).find(x => x.key === key);
  if (!d) return;
  if (NOW && NOW.key === 'dg:' + key) { togglePlay(); return; }
  savePosition(true);
  stopAt = null;
  if (playingBtn) { playingBtn.classList.remove('on'); playingBtn = null; }
  /* Chapters fall out of the pieces: each one's length is known, so the
     boundaries are just the running total. */
  let acc = 0, lastStory = null;
  NPCH = d.pieces.map(p => {
    const isTake = p.story && p.story === lastStory;
    lastStory = p.story || null;
    const c = {title: (isTake ? '↳ ' : '') + p.topic, at: acc,
               show: p.feed_title || p.feed_slug};
    acc += p.seconds; return c;
  });
  NOW = {digest: true, key: 'dg:' + key, title: d.title, feed_title: 'Digest',
         result_seconds: acc};
  audio.src = `/digests/${key}.mp3`;
  audio.playbackRate = RATE;
  audio.play().catch(() => toast('Playback blocked — tap play again'));
  syncMini();
  setMediaSession(NOW);
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
  document.getElementById('np-rate').textContent = RATE + '×';
}

function syncMini() {
  const on = !!NOW;
  document.getElementById('mini').classList.toggle('on', on);
  document.body.classList.toggle('playing', on);
  // A brief is stitched from many episodes, so there is no single episode
  // clock to convert a position against — nothing to save a clip from.
  const clipBtn = document.getElementById('np-clip');
  if (clipBtn) clipBtn.disabled = !on || !!NOW.digest;
  if (!on) { closeNP(); return; }
  document.getElementById('m-title').textContent = NOW.title || '';
  const img = document.getElementById('m-art');
  const art = NOW.digest ? null : feedArt(NOW.feed_slug);
  if (art) { img.src = art; img.style.display = ''; }
  else { img.removeAttribute('src'); img.style.display = 'none'; }
}

function updateMini() {
  if (!NOW) return;
  const cur = audio.currentTime || 0, dur = audio.duration || NOW.result_seconds || 0;
  const icon = audio.paused ? '▶' : '❚❚';
  document.getElementById('m-play').textContent = icon;
  document.getElementById('np-play').textContent = icon;
  if (dur) document.getElementById('m-bar').style.width =
    Math.min(100, cur / dur * 100) + '%';
  if (npOpen()) {
    document.getElementById('np-cur').textContent = clock(cur);
    document.getElementById('np-left').textContent =
      dur ? '−' + clock(Math.max(0, dur - cur)) : '';
    if (!scrubbing && dur) {
      document.getElementById('scrub').value = Math.round(cur / dur * 1000);
    }
    updateChapters(cur);
  }
  const b = document.getElementById('ep-play');
  if (b && VIEW && VIEW.kind === 'episode') b.textContent = playLabel(VIEW.key);
}

/* ---- now playing sheet ---- */
function npOpen() { return document.getElementById('np').classList.contains('on'); }

function openNP() {
  if (!NOW) return;
  document.getElementById('np-from').textContent =
    NOW.digest ? 'Your digest' : 'Playing from ' + (NOW.feed_title || NOW.feed_slug || '');
  document.getElementById('np-title').textContent = NOW.title || '';
  document.getElementById('np-show').textContent =
    NOW.digest ? 'Assembled from your shows' : (NOW.feed_title || NOW.feed_slug || '');
  const img = document.getElementById('np-art');
  const art = NOW.digest ? null : feedArt(NOW.feed_slug);
  if (art) { img.src = art; img.style.display = ''; }
  else { img.removeAttribute('src'); img.style.display = 'none'; }
  document.getElementById('np-rate').textContent = RATE + '×';
  document.getElementById('np-cut').textContent =
    NOW.digest ? '' : (removedLabel(NOW) ? '✂️ ' + removedLabel(NOW) : '');
  document.getElementById('np').classList.add('on');
  ensureChapters();
  updateMini();
}
function closeNP() { document.getElementById('np').classList.remove('on'); }

/* Chapters come from the summary's topic index; the episode list payload does
   not carry it, so fetch the detail once per played episode and cache it. */
async function ensureChapters() {
  if (!NOW || NOW.digest || NPCH) { updateChapters(audio.currentTime || 0); return; }
  if (NOW._chapters !== undefined) { NPCH = NOW._chapters; return; }
  NOW._chapters = null;
  try {
    const d = await api('/api/episodes/' + NOW.key);
    NOW._chapters = ((d.index && d.index.topics) || [])
      .filter(t => t.at_cut != null)
      .map(t => ({title: t.title, at: t.at_cut}));
  } catch (e) { NOW._chapters = null; }
  NPCH = NOW._chapters;
  updateChapters(audio.currentTime || 0);
}

let NP_JUMP = null;
function updateChapters(cur) {
  const box = document.getElementById('np-chap');
  const next = document.getElementById('np-next');
  if (!NPCH || !NPCH.length) { box.classList.remove('on'); next.classList.remove('on'); return; }
  let i = -1;
  NPCH.forEach((c, j) => { if (c.at <= cur + 1) i = j; });
  if (i >= 0) {
    box.classList.add('on');
    document.getElementById('np-chap-n').textContent = `Chapter ${i + 1} of ${NPCH.length}`;
    document.getElementById('np-chap-t').textContent = NPCH[i].title;
  } else box.classList.remove('on');
  const n = NPCH[i + 1];
  if (n) {
    next.classList.add('on');
    document.getElementById('np-next-t').textContent = `${n.title} · ${clock(n.at)}`;
    NP_JUMP = n.at;
  } else { next.classList.remove('on'); NP_JUMP = null; }
}
document.getElementById('np-next').onclick = () => {
  if (NP_JUMP != null) { audio.currentTime = NP_JUMP; updateMini(); }
};

function savePosition(force) {
  if (!NOW || NOW.digest) return;
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
  const art = ep.digest ? null : feedArt(ep.feed_slug);
  navigator.mediaSession.metadata = new MediaMetadata({
    title: ep.title || 'skipcast',
    artist: ep.feed_title || 'skipcast',
    album: ep.cut_speakers ? `${Math.round((ep.cut_seconds||0)/60)} min removed` : '',
    artwork: art ? [{src: art}]
                 : [{src: '/icon.svg', sizes: '512x512', type: 'image/svg+xml'}],
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
    if (playingBtn) { playingBtn.classList.remove('on'); playingBtn = null; }
    return;
  }
  if (NOW) { updateMini(); savePosition(false); }
});
audio.addEventListener('play', () => { updateMini(); if (NOW) setPlaybackState('playing'); });
audio.addEventListener('pause', () => { updateMini(); savePosition(true);
                                        if (NOW) setPlaybackState('paused'); });
audio.addEventListener('ended', () => { savePosition(true); render(); });
audio.addEventListener('error', () => { if (NOW) toast('Could not load that audio'); });
function setPlaybackState(s) {
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = s;
}

document.getElementById('scrub').addEventListener('input', e => {
  scrubbing = true;
  const dur = audio.duration || 0;
  if (dur) document.getElementById('np-cur').textContent =
    clock(e.target.value / 1000 * dur);
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

function playLabel(key) {
  if (NOW && NOW.key === key && !audio.paused) return '❚❚ Pause';
  const ep = (LISTEN || []).find(x => x.key === key);
  return ep && !ep.finished && (ep.position || 0) > 30 ? '▶ Resume' : '▶ Play';
}

/* ---- data ------------------------------------------------------------- */
async function refresh(silent) {
  try {
    STATE = await api('/api/state');
    const running = STATE.jobs.filter(j => j.status === 'running' || j.status === 'queued');
    const b = document.getElementById('jobbadge');
    b.style.display = running.length ? 'block' : 'none';
    b.textContent = running.length;
    // A job finishing means whatever screen the user is on may be stale.
    // Reload it in place rather than sending anyone to a log.
    if (running.length < LASTRUN) {
      DIGESTS = null;
      if (VIEW && VIEW.kind === 'feed') reloadFeed(VIEW.slug);
      if (VIEW && VIEW.kind === 'episode') reloadEpisode(VIEW.key);
      if (TAB === 'home' && !VIEW) loadDigests();
      loadPeople();
    }
    LASTRUN = running.length;
    loadListen();
    if (!silent) render();
    else if (TAB === 'jobs' || (VIEW && VIEW.kind === 'job')) render();
    // Poll faster while something is working.
    clearInterval(TIMER);
    TIMER = setInterval(() => refresh(true), running.length ? 3000 : 20000);
  } catch (e) { toast('Cannot reach server'); }
}

let LISTEN = null;
async function loadListen() {
  const first = LISTEN === null;
  try {
    const d = await api('/api/episodes');
    LISTEN = d.episodes;
    // Keep the playing episode's live position rather than the stored one.
    if (NOW && !NOW.digest) {
      const fresh = LISTEN.find(e => e.key === NOW.key);
      if (fresh) {
        fresh.position = NOW.position; fresh._chapters = NOW._chapters;
        NOW = fresh;
      }
    }
  } catch (e) { LISTEN = []; }
  // Never repaint under someone mid-search: it would eat their keystrokes.
  if (TAB === 'home' && !VIEW && !SEARCH.active) render();
  // Play-pill labels on show pages come from LISTEN ("25m left" vs "1h 9m");
  // a deep-linked show page can render before the first LISTEN load answers.
  else if (first && VIEW && VIEW.kind === 'feed') render();
}

let DIGESTS = null;
async function loadDigests() {
  try { DIGESTS = (await api('/api/digests')).digests; }
  catch (e) { DIGESTS = []; }
  if (TAB === 'home' && !VIEW && !SEARCH.active) render();
}

let PEOPLE = null;
async function loadPeople() {
  try { PEOPLE = (await api('/api/persons')).persons; }
  catch (e) { PEOPLE = []; }
  if ((TAB === 'library' || TAB === 'home') && !VIEW && !SEARCH.active) render();
}

let WATCH = null;
async function loadWatch() {
  try { WATCH = (await api('/api/watchlist')).watchlist; }
  catch (e) { WATCH = []; }
  if (TAB === 'home' && !VIEW && SEARCH.active) renderSearchBody();
}

/* ---- views ------------------------------------------------------------ */
/* The compact bar's title mirrors wherever render is about to paint. */
function syncTopbar() {
  const t = VIEW
    ? (VIEW.kind === 'feed' ? ((VIEW.data && VIEW.data.feed.title) || '')
       : VIEW.kind === 'episode' ? ((VIEW.data && VIEW.data.title) || '')
       : VIEW.kind === 'job' ? 'Activity'
       : 'Add a show')
    : ({home: 'Today', library: 'Library', jobs: 'Activity'})[TAB] || '';
  document.getElementById('topbar-t').textContent = t;
}

function render() {
  syncTopbar();
  // A deep link can route before /api/state has answered; refresh() renders
  // again the moment it does.
  if (!STATE) { el.innerHTML = '<div class="empty"><span class="spin"></span></div>'; return; }
  if (VIEW && VIEW.kind === 'feed') return renderFeed(VIEW);
  if (VIEW && VIEW.kind === 'episode') return renderEpisode(VIEW);
  if (VIEW && VIEW.kind === 'job') return renderJob(VIEW);
  if (VIEW && VIEW.kind === 'add') return renderAdd();
  if (TAB === 'home') return renderHome();
  if (TAB === 'library') return renderLibrary();
  if (TAB === 'clips') return renderClips();
  if (TAB === 'jobs') return renderJobs();
}

/* ---- home ------------------------------------------------------------- */
const SEARCH = {q: '', active: false, busy: false, error: '',
                moments: null, facts: null};
let SEARCH_TIMER = null;

function freshOf(list) {
  return list.filter(e => !e.finished && (e.position || 0) <= 30);
}

function renderHome() {
  const running = STATE.jobs.filter(j => j.status === 'running' || j.status === 'queued');
  const briefJob = STATE.jobs.find(j => j.kind === "digest" && (j.status === "queued" || j.status === "running"));
  el.innerHTML = `
    <div class="bigtitle"><h2>Today</h2><span class="spacer"></span>
      ${running.length ? `<button class="runpill" onclick="go('jobs')">
        <span class="spin"></span> ${running.length} running</button>` : ''}</div>
    <div class="sub" style="margin:2px 0 10px">Make one focused brief from recent, unheard moments across your shows.</div>
    ${briefJob ? `<div class="card pad" style="margin:0 0 16px"><div class="title">Preparing your brief</div>
      <div class="sub">${esc(briefJob.progress || briefJob.label || "Waiting to start")} &middot; <a onclick="go('jobs')">View activity</a></div></div>` :
      `<div class="wrap" style="margin:0 0 16px">${[15, 30, 45, 60].map(m =>
        `<button class="btn" onclick="buildDigest(${m})">I have ${m} min</button>`).join('')}</div>`}
    <div class="searchrow">
      <div class="search">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#9a9fad"
          stroke-width="2.4"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input id="hq" type="search" placeholder="Shows, people, or anything said…"
          autocapitalize="none" autocorrect="off" enterkeyhint="search"
          value="${esc(SEARCH.q)}">
      </div>
      ${SEARCH.active ? `<button class="cancel" onclick="cancelSearch()">Cancel</button>` : ''}
    </div>
    <div id="hb"></div>`;
  const inp = document.getElementById('hq');
  inp.addEventListener('focus', () => {
    if (!SEARCH.active) { SEARCH.active = true; render(); }
  });
  inp.addEventListener('input', () => {
    SEARCH.q = inp.value;
    clearTimeout(SEARCH_TIMER);
    SEARCH_TIMER = setTimeout(runSearch, 350);
  });
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { clearTimeout(SEARCH_TIMER); runSearch(); }
  });
  if (SEARCH.active) {
    renderSearchBody();
    if (WATCH === null) loadWatch();
  } else {
    document.getElementById('hb').innerHTML = browseHtml();
  }
}

function cancelSearch() {
  SEARCH.active = false; SEARCH.q = ''; SEARCH.moments = null; SEARCH.facts = null;
  render();
}

function browseHtml() {
  if (LISTEN === null) return '<div class="empty"><span class="spin"></span></div>';
  if (!LISTEN.length && !(STATE.feeds || []).length) {
    return `<div class="empty">Nothing here yet.<br><br>
      <button class="btn primary" onclick="openAdd()">Add your first show</button></div>`;
  }
  if (!LISTEN.length) {
    return `<div class="empty">Your shows are added, but nothing is ready for a brief yet.<br><br>
      New episodes need to finish processing before Skipcast can build a brief.<br><br>
      <button class="btn primary" onclick="go('library')">Check your shows</button></div>`;
  }
  const cont = LISTEN.filter(e => !e.finished && (e.position || 0) > 30);
  const played = LISTEN.filter(e => e.finished);
  const fresh = freshOf(LISTEN);
  return digestHtml() +
    (cont.length ? `
    <div class="sec"><h4>Continue</h4></div>
    <div class="carousel">${cont.map(contCard).join('')}</div>` : '') +
    shelfHtml() + `
    <div class="sec"><h4>New episodes</h4></div>
    <div class="card">${fresh.length ? fresh.map(epRow).join('')
      : '<div class="empty">Nothing unplayed. Fetch more from a show in your library.</div>'}</div>` +
    (played.length ? `
    <div class="card">
      <h2 class="fold" onclick="UI.playedOpen=!UI.playedOpen;render()">
        <span>Played<span class="s">${played.length} episode${played.length === 1 ? '' : 's'}</span></span>
        <span class="ch">${UI.playedOpen ? '▾' : '▸'}</span></h2>
      ${UI.playedOpen ? `<div class="foldbody">${played.map(epRow).join('')}</div>` : ''}
    </div>` : '');
}

function contCard(e) {
  const dur = e.result_seconds || 0, pos = e.position || 0;
  const pct = dur ? Math.min(100, pos / dur * 100) : 0;
  return `
    <div class="ccard" onclick="openEpisode('${esc(e.key)}')">
      <div class="art" onclick="event.stopPropagation();playEpisode('${esc(e.key)}')">
        <span class="ini" style="font-size:26px">${esc((e.feed_title || e.feed_slug || '?')
          .slice(0, 2).toUpperCase())}</span>
        ${feedArt(e.feed_slug) ? `<img src="${esc(feedArt(e.feed_slug))}" alt=""
          loading="lazy" onerror="this.remove()">` : ''}
        <div class="play">${NOW && NOW.key === e.key && !audio.paused ? '❚❚' : '▶'}</div>
      </div>
      <div class="t">${esc(e.title)}</div>
      <div class="s">${esc(e.feed_title || e.feed_slug)} ·
        ${mins(Math.max(0, dur - pos))} left</div>
      <div class="prog"><i style="width:${pct}%"></i></div>
    </div>`;
}

function epRow(e) {
  const dur = e.result_seconds || 0, pos = e.position || 0;
  const label = e.finished ? '↺'
    : pos > 30 ? `▶ ${mins(Math.max(0, dur - pos))} left` : `▶ ${mins(dur)}`;
  const when = dateShort(e.published_ts);
  return `
    <div class="eprow" onclick="openEpisode('${esc(e.key)}')">
      ${artHtml(feedArt(e.feed_slug), e.feed_title || e.feed_slug, '', 15)}
      <div class="grow">
        <div class="t">${esc(e.title)}</div>
        <div class="s">${when ? when + ' · ' : ''}${esc(e.feed_title || e.feed_slug)}${
          removedLabel(e) ? ' · ' + removedLabel(e) : ''}</div>
      </div>
      <button class="pbtn ${NOW && NOW.key === e.key && !audio.paused ? 'on' : ''}"
        onclick="event.stopPropagation();playEpisode('${esc(e.key)}')">${label}</button>
    </div>`;
}

/* The answer to "I have 35 minutes" — one file made of topic-sized pieces
   from across the library, grouped so takes on the same story sit together.
   A ready digest gets the hero treatment; the builder, the archive and each
   digest's rundown (with the why behind every piece) hide behind the
   chevron. */
function digestStats(d) {
  const stories = new Set(d.pieces.map(p => p.story
    || 's' + p.episode_key + p.start)).size;
  const shows = new Set(d.pieces.map(p => p.feed_slug)).size;
  return `${mins(d.seconds || d.pieces.reduce((a, p) => a + p.seconds, 0))}
    · ${stories} stor${stories === 1 ? 'y' : 'ies'}
    from ${shows} show${shows === 1 ? '' : 's'}`;
}

/* The rundown: what plays, in order, and why each piece earned its slot.
   Numbered like the episode chapters; second takes on a story indent under
   their story's first take. */
function digestPieces(d) {
  let lastStory = null, no = 0;
  return `<div class="rundown">` + d.pieces.map(p => {
    const isTake = p.story && p.story === lastStory;
    if (!isTake) no++;
    lastStory = p.story || null;
    const len = p.excerpt_of_seconds
      ? `${Math.round(p.seconds / 60)}m of ${Math.round(p.excerpt_of_seconds / 60)}m`
      : `${Math.round(p.seconds / 60)}m`;
    return `
      <div class="chapter ${isTake ? 'take' : ''}">
        ${isTake ? '' : `<div class="no">${no}</div>`}
        <div class="t">${isTake ? '↳ ' : ''}${esc(p.topic)}
          <span class="why">${esc(p.why || p.feed_title || p.feed_slug)}</span></div>
        <span class="len">${len}</span>
      </div>`;
  }).join('') + `</div>`;
}

function digestHtml() {
  const latest = DIGESTS && DIGESTS.length ? DIGESTS[0] : null;
  const builtOn = latest && latest.created_at
    ? new Date(latest.created_at).toLocaleDateString(undefined, {day: "numeric", month: "short"})
    : "";
  const head = latest ? `
    <div class="row" role="button" tabindex="0" aria-expanded="${UI.digestOpen}"
      onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();UI.digestOpen=!UI.digestOpen;render()}"
      onclick="UI.digestOpen=!UI.digestOpen;render()">
      <div class="ic">◍</div>
      <div class="grow"><div class="t">Latest brief</div>
      <div class="s">${digestStats(latest)} &middot; built from unheard moments${builtOn ? " &middot; " + builtOn : ""}
        <span style="opacity:.75; margin-left:4px">${UI.digestOpen ? '▾' : '▸'}</span></div>
      ${latest.theme ? `<div class="s" style="white-space:nowrap; overflow:hidden;
        text-overflow:ellipsis">${esc(latest.theme)}</div>` : ''}</div>
      <button class="go" aria-label="Play latest brief" onclick="event.stopPropagation();playDigest('${esc(latest.key)}')">
        ${NOW && NOW.key === 'dg:' + latest.key && !audio.paused ? '❚❚' : '▶'}</button>
    </div>` : `
    <div class="row" role="button" tabindex="0" aria-expanded="${UI.digestOpen}"
      onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();UI.digestOpen=!UI.digestOpen;render()}"
      onclick="UI.digestOpen=!UI.digestOpen;render()">
      <div class="ic">◍</div>
      <div class="grow"><div class="t">Build your brief</div>
      <div class="s">One focused thing to play for the time you have</div></div>
      <span style="opacity:.8">${UI.digestOpen ? '▾' : '▸'}</span>
    </div>`;
  const hero = `<div class="digest">${head}</div>`;
  if (!UI.digestOpen) return hero;
  if (DIGESTS === null) {
    loadDigests();
    return hero + `<div class="card"><div class="empty"><span class="spin"></span></div></div>`;
  }
  return hero + `
    <div class="card pad" style="margin-bottom:20px">
      <div class="sub" style="margin-bottom:10px">Past briefs are kept here. Each one was built from recent unheard topics, with related coverage kept together.</div>
      ${DIGESTS.map(d => `
        <div class="item">
          <div class="row">
            <div class="grow"><div class="title" style="font-size:14px">${esc(d.title)}</div>
              <div class="sub">${digestStats(d)}${d.theme ? ' · ' + esc(d.theme) : ''}</div></div>
            <button class="pbtn" onclick="playDigest('${esc(d.key)}')">▶</button>
            <button class="pbtn" onclick="removeDigest('${esc(d.key)}')">✕</button>
          </div>
          ${digestPieces(d)}
        </div>`).join('')}
      ${DIGESTS.length ? `<div class="sub" style="margin-top:10px">Subscribe in your
        podcast app: <code>${esc(STATE.base_url)}/digests.xml</code></div>` : ''}
    </div>`;
}

async function buildDigest(minutes) {
  if (STATE.jobs.some(j => j.kind === "digest" && (j.status === "queued" || j.status === "running"))) {
    toast("A brief is already being prepared");
    return;
  }
  try {
    await api('/api/digests', {method:'POST', body:{minutes}});
    toast('Assembling your digest — it will appear here in a few minutes');
    await refresh(true);
  } catch (e) { toast(e.message); }
}
async function removeDigest(key) {
  try {
    await api('/api/digests/' + key, {method:'DELETE'});
    await loadDigests(); render();
  } catch (e) { toast(e.message); }
}

function shelfHtml() {
  const feeds = STATE.feeds || [];
  const persons = PEOPLE || [];
  if (!feeds.length && !persons.length) return '';
  const freshBy = {};
  freshOf(LISTEN || []).forEach(e => {
    freshBy[e.feed_slug] = (freshBy[e.feed_slug] || 0) + 1;
  });
  return `
    <div class="sec"><h4>Your shows</h4><a onclick="go('library')">Library</a></div>
    <div class="shelf">
      ${feeds.map(f => `
        <div class="show" onclick="openFeed('${esc(f.slug)}')">
          <div class="art">
            <span class="ini">${esc((f.title || f.slug).slice(0, 2).toUpperCase())}</span>
            ${f.image_url ? `<img src="${esc(f.image_url)}" alt=""
              loading="lazy" onerror="this.remove()">` : ''}
            ${freshBy[f.slug] ? `<span class="new">${freshBy[f.slug]}</span>` : ''}
          </div>
          <div class="n">${esc(f.title || f.slug)}</div>
        </div>`).join('')}
      ${persons.map(p => `
        <div class="show" onclick="openPerson('${esc(p.speaker)}')">
          <div class="art person"><span class="ini">${esc(p.speaker.split(/\s+/)
            .slice(0,2).map(w => w[0] || '').join('').toUpperCase())}</span></div>
          <div class="n">${esc(p.speaker)}</div>
        </div>`).join('')}
    </div>`;
}

function openPerson(name) {
  UI.libSeg = 'people'; UI.personOpen = name;
  go('library');
}

/* ---- search ------------------------------------------------------------ */
async function runSearch() {
  const q = SEARCH.q.trim();
  if (!q) { SEARCH.moments = null; SEARCH.facts = null; renderSearchBody(); return; }
  SEARCH.busy = true; SEARCH.error = '';
  renderSearchBody();
  try {
    const [words, facts] = await Promise.allSettled([
      api('/api/transcripts/search?q=' + encodeURIComponent(q)),
      api('/api/entities?q=' + encodeURIComponent(q)),
    ]);
    if (SEARCH.q.trim() !== q) return;  // stale response; a newer search ran
    SEARCH.moments = words.status === 'fulfilled' ? words.value.results : [];
    SEARCH.facts = facts.status === 'fulfilled' ? facts.value.results : [];
    if (words.status === 'rejected' && facts.status === 'rejected') {
      SEARCH.error = words.reason.message;
    }
  } catch (e) { SEARCH.error = e.message; }
  SEARCH.busy = false;
  renderSearchBody();
}

function renderSearchBody() {
  const box = document.getElementById('hb');
  if (box) box.innerHTML = searchBodyHtml();
}

function watchChips() {
  const q = SEARCH.q.trim();
  const items = WATCH || [];
  const watched = items.some(w => w.term.toLowerCase() === q.toLowerCase());
  return `<div class="chiprow">
    ${q && !watched ? `<span class="chip watch"
      onclick='addWatch(${JSON.stringify(q)})'>👁 Watch “${esc(q)}”</span>` : ''}
    ${items.map(w => `<span class="chip"
      onclick='chipSearch(${JSON.stringify(w.term)})'>${esc(w.term)}
      ${w.new ? `<span class="n">· ${w.new} new</span>` : ''}
      <span class="x" onclick='event.stopPropagation();unwatch(${JSON.stringify(w.term)})'>✕</span>
    </span>`).join('')}
    ${items.reduce((a, w) => a + w.new, 0) ? `<span class="chip"
      onclick="markWatchSeen()">Mark seen</span>` : ''}
  </div>`;
}

function chipSearch(term) {
  SEARCH.q = term;
  const inp = document.getElementById('hq');
  if (inp) inp.value = term;
  runSearch();
}

function searchBodyHtml() {
  const q = SEARCH.q.trim();
  const idx = (STATE && STATE.search) || {passages: 0, episodes: 0};
  let out = watchChips();
  if (!q) {
    return out + `<div class="empty">${idx.episodes
      ? `Search anything anyone said — a phrase, a ticker, a name.
         <br><br><span class="sub">${idx.passages.toLocaleString()} passages from
         ${idx.episodes} episode${idx.episodes === 1 ? '' : 's'} ·
         <a class="act" onclick="rebuildIndex()">rebuild</a></span>`
      : `Nothing searchable yet — episodes become searchable once transcribed.
         <br><br><a class="act" onclick="rebuildIndex()">Rebuild index</a>`}</div>`;
  }
  if (SEARCH.busy && SEARCH.moments === null) {
    return out + '<div class="empty"><span class="spin"></span> Searching…</div>';
  }
  if (SEARCH.error) return out + `<div class="empty">${esc(SEARCH.error)}</div>`;
  if (SEARCH.moments === null) return out;

  // Shows and episodes match on titles, locally — no endpoint needed.
  const ql = q.toLowerCase();
  const shows = (STATE.feeds || []).filter(
    f => (f.title || f.slug).toLowerCase().includes(ql));
  const eps = (LISTEN || []).filter(e => (e.title || '').toLowerCase().includes(ql))
    .slice(0, 5);

  if (shows.length) {
    out += `<div class="sec"><h4 style="font-size:16px">Shows</h4></div>
      <div class="card">${shows.map(f => `
        <div class="eprow" onclick="openFeed('${esc(f.slug)}')">
          ${artHtml(f.image_url, f.title || f.slug, '', 15)}
          <div class="grow"><div class="t">${esc(f.title || f.slug)}</div>
          <div class="s">${f.ready_count} episodes ready</div></div>
        </div>`).join('')}</div>`;
  }
  if (SEARCH.moments.length) {
    out += `<div class="sec"><h4 style="font-size:16px">Moments</h4>
        <span class="hint">what was said</span></div>
      <div class="card">${SEARCH.moments.slice(0, 12).map(r => `
        <div class="hit">
          <div class="q">${r.snippet_html}</div>
          <div class="m">${esc(r.speaker)} · ${esc(r.episode_title)} ·
            ${r.removed
              ? `cut from your copy · <span class="at"
                  onclick="play(this,'${esc(r.episode_key)}',${r.start},${r.start + 25})">▶ original</span>`
              : `<span class="at"
                  onclick="playEpisode('${esc(r.episode_key)}',${r.at_cut})">▶ ${clock(r.at_cut)}</span>`}
            · <span class="at" onclick="openEpisode('${esc(r.episode_key)}')">episode</span>
          </div>
        </div>`).join('')}</div>`;
  }
  if (SEARCH.facts.length) {
    out += `<div class="sec"><h4 style="font-size:16px">Specifics</h4>
        <span class="hint">what summaries kept</span></div>
      <div class="card">${SEARCH.facts.slice(0, 12).map(r => `
        <div class="hit">
          <div class="q" style="font-weight:650">${esc(r.value)}
            <span class="pill">${esc(r.type)}</span>
            ${r.confidence && r.confidence !== 'firm'
              ? `<span class="pill" style="color:var(--warn)">${esc(r.confidence)}</span>` : ''}</div>
          ${r.detail ? `<div class="m">${esc(r.detail)}</div>` : ''}
          <div class="m">${r.speaker ? esc(r.speaker) + ' · ' : ''}${esc(r.episode_title)}
            ${r.at_cut != null ? (r.removed
              ? ` · <span class="at" onclick="play(this,'${esc(r.episode_key)}',${r.at_seconds},${r.at_seconds + 25})">▶ original</span>`
              : ` · <span class="at" onclick="playEpisode('${esc(r.episode_key)}',${r.at_cut})">▶ ${clock(r.at_cut)}</span>`) : ''}
            · <span class="at" onclick="openEpisode('${esc(r.episode_key)}')">episode</span>
          </div>
        </div>`).join('')}</div>`;
  }
  if (eps.length) {
    out += `<div class="sec"><h4 style="font-size:16px">Episodes</h4></div>
      <div class="card">${eps.map(epRow).join('')}</div>`;
  }
  if (!shows.length && !SEARCH.moments.length && !SEARCH.facts.length && !eps.length) {
    out += `<div class="empty">Nothing matched “${esc(q)}”.</div>`;
  }
  out += `<div class="sub" style="text-align:center; margin-bottom:20px">
    ${idx.passages.toLocaleString()} passages indexed ·
    <a class="act" onclick="rebuildIndex()">Rebuild</a></div>`;
  return out;
}

async function addWatch(term) {
  try {
    await api('/api/watchlist', {method:'POST', body:{term}});
    toast(`Watching ${term}`);
    await loadWatch(); renderSearchBody();
  } catch (e) { toast(e.message); }
}
async function unwatch(term) {
  try {
    await api('/api/watchlist/' + encodeURIComponent(term), {method:'DELETE'});
    await loadWatch(); renderSearchBody();
  } catch (e) { toast(e.message); }
}
async function markWatchSeen() {
  try {
    await api('/api/watchlist/seen', {method:'POST', body:{}});
    await loadWatch(); renderSearchBody();
  } catch (e) { toast(e.message); }
}
async function rebuildIndex() {
  try {
    await api('/api/reindex', {method:'POST'});
    toast('Rebuilding the search index — a minute or two');
    await refresh(true);
  } catch (e) { toast(e.message); }
}

/* ---- library ----------------------------------------------------------- */
function renderLibrary() {
  if (PEOPLE === null) loadPeople();
  if (TOPICS === null && UI.libSeg === 'topics') loadTopics();
  const running = STATE.jobs.filter(j => j.status === 'running' || j.status === 'queued');
  const seg = UI.libSeg;
  el.innerHTML = `
    <div class="bigtitle"><h2>Library</h2><span class="spacer"></span>
      ${running.length ? `<button class="runpill" onclick="go('jobs')">
        <span class="spin"></span> ${running.length}</button>` : ''}
      <button class="addbtn" onclick="openAdd()">＋</button></div>
    <div class="seg">
      <button class="${seg === 'shows' ? 'on' : ''}"
        onclick="UI.libSeg='shows';render()">Shows</button>
      <button class="${seg === 'people' ? 'on' : ''}"
        onclick="UI.libSeg='people';render()">People</button>
      <button class="${seg === 'topics' ? 'on' : ''}"
        onclick="UI.libSeg='topics';render()">Topics</button>
    </div>
    ${seg === 'shows' ? showsGridHtml()
      : seg === 'people' ? peopleHtml() : topicsHtml()}`;
}

function showsGridHtml() {
  const feeds = STATE.feeds || [];
  const freshBy = {};
  freshOf(LISTEN || []).forEach(e => {
    freshBy[e.feed_slug] = (freshBy[e.feed_slug] || 0) + 1;
  });
  const policy = f => {
    const eps = (LISTEN || []).filter(e => e.feed_slug === f.slug);
    const names = new Set();
    eps.forEach(e => (e.cut_speakers || '').split(',').forEach(n => {
      n = n.trim(); if (n) names.add(n.split(' ')[0]);
    }));
    if (names.size) return [...names].join(', ') + ' removed';
    const ads = eps.some(e => (e.interstitial_seconds || 0) > 30);
    return ads ? 'ads removed' : `${f.ready_count}/${f.total_count} ready`;
  };
  return `<div class="lgrid">
    ${feeds.map(f => `
      <div class="lcell" onclick="openFeed('${esc(f.slug)}')">
        <div class="art">
          <span class="ini" style="font-size:26px">${esc((f.title || f.slug)
            .slice(0, 2).toUpperCase())}</span>
          ${f.image_url ? `<img src="${esc(f.image_url)}" alt=""
            loading="lazy" onerror="this.remove()">` : ''}
          ${freshBy[f.slug] ? `<span class="new">${freshBy[f.slug]}</span>` : ''}
        </div>
        <div class="n">${esc(f.title || f.slug)}</div>
        <div class="s">${esc(policy(f))}</div>
      </div>`).join('')}
    <div class="lcell" onclick="openAdd()">
      <div class="art" style="background:var(--card2)">
        <span class="ini" style="font-size:30px; color:var(--muted)">＋</span></div>
      <div class="n" style="color:var(--muted)">Add a show</div>
      <div class="s">search or RSS</div>
    </div>
  </div>`;
}

/* ---- topics ------------------------------------------------------------ */
/* Terms with an opinion attached, pointing either way: watch surfaces
   mentions, skip removes the chapters that are about them. One list, because
   they are one concept — and because a term you stop watching is often the
   one you are about to start skipping. */
let TOPICS = null;

async function loadTopics() {
  try { TOPICS = await api('/api/topics'); }
  catch (e) { TOPICS = {topics: [], rules: []}; }
  if (TAB === 'library' && UI.libSeg === 'topics' && !VIEW) render();
}

function topicsHtml() {
  if (TOPICS === null) { loadTopics(); return '<div class="empty"><span class="spin"></span></div>'; }
  const rules = TOPICS.rules || [];
  return `
    <div class="row" style="margin-bottom:14px">
      <input type="text" class="grow" id="newterm" autocapitalize="none"
             placeholder="Add a topic — a team, a company, a segment…">
      <button class="btn" onclick="addTerm()">Add</button>
    </div>
    ${TOPICS.topics.length ? `<div class="card">${TOPICS.topics.map(t => {
      const ex = rules.filter(r => r.term_norm === t.term.toLowerCase());
      const bits = [];
      if (t.state === 'skip') {
        bits.push(t.episodes
          ? `skipped · ${t.episodes} chapter${t.episodes === 1 ? '' : 's'}
             · saved ${mins(t.seconds)}`
          : 'skipped · nothing cut yet');
      } else {
        bits.push(`watching · ${t.mentions} mention${t.mentions === 1 ? '' : 's'}`);
        if (t.new) bits.push(`<span style="color:var(--ok)">${t.new} new</span>`);
      }
      // Counted rather than named: show titles are long enough to push the
      // three-state pill off the row, and the show's own page is where an
      // exception is read and changed anyway.
      if (ex.length) bits.push(
        `${ex.length} exception${ex.length === 1 ? '' : 's'}`);
      const n = JSON.stringify(t.term);
      return `
        <div class="item row">
          <div class="grow"><div class="t">${esc(t.term)}</div>
            <div class="s">${bits.join(' · ')}</div></div>
          <div class="tri">
            <button class="w ${t.state === 'watch' ? 'on' : ''}"
              onclick='setTermState(${n}, ${t.state === 'watch' ? 'null' : '"watch"'})'>Watch</button>
            <button class="k ${t.state === 'skip' ? 'on' : ''}"
              onclick='setTermState(${n}, ${t.state === 'skip' ? 'null' : '"skip"'})'>Skip</button>
          </div>
        </div>`;
    }).join('')}</div>`
    : `<div class="empty">No topics yet.<br><br>Add a team, a company or a
       recurring segment — watch it to hear when it comes up, or skip it to cut
       the chapters that are about it.</div>`}
    <div class="sub" style="padding:0 2px">Skips remove whole chapters once an
      episode is summarised. Mentions inside other chapters stay. Exceptions per
      show live on each show's page.</div>`;
}

function addTerm() {
  const field = document.getElementById('newterm');
  const term = (field ? field.value : '').trim();
  if (!term) { toast('Type a topic first'); if (field) field.focus(); return; }
  if (field) field.value = '';
  // A new term starts by being watched: the harmless direction. Skipping is
  // one more tap, and that tap gets the impact preview.
  setTermState(term, 'watch');
}

/* Turning a skip on or off is the one control here that edits audio, so it
   never fires straight from the tap — it shows what would change first. */
async function setTermState(term, state) {
  if (state !== 'skip') return applyTermState(term, state);
  let d;
  try { d = await api('/api/topics/impact?term=' + encodeURIComponent(term)); }
  catch (e) { return toast(e.message); }
  const body = d.count ? `<div class="card" style="margin:8px 0 0">${
    d.chapters.map(c => `
      <div class="item row">
        <div class="grow"><div class="t" style="font-size:13.5px">${esc(c.title)}</div>
          <div class="s">${esc(c.feed_title)} · ${dateShort(c.published_ts)}
            · ${mins(c.seconds)}</div></div>
        <button class="pbtn" onclick="play(this,'${esc(c.episode_key)}',${c.at_seconds},${c.at_seconds + 25})">▶</button>
      </div>`).join('')}</div>`
    : '';
  askSheet(`Skip “${esc(term)}”?`,
    d.count
      ? `Would remove ${d.count} chapter${d.count === 1 ? '' : 's'} across
         ${d.episodes} episode${d.episodes === 1 ? '' : 's'} · ${mins(d.seconds)}.
         Tap ▶ to hear what goes. Applies to future episodes automatically;
         skipped audio stays on disk and stays checkable.`
      : `Nothing in your library matches that yet — it will apply to future
         episodes.`,
    body,
    d.episodes ? `Skip · re-cut ${d.episodes}` : 'Skip it',
    () => applyTermState(term, 'skip'));
}

async function applyTermState(term, state) {
  try {
    const r = await api('/api/topics', {method:'POST', body:{term, state}});
    toast(r.recutting
      ? `Re-cutting ${r.recutting} episode${r.recutting === 1 ? '' : 's'} — they update here when done`
      : (state === 'skip' ? `Skipping ${term}`
         : state === 'watch' ? `Watching ${term}` : `Forgot ${term}`));
    await loadTopics();
    await refresh(true);
    render();
  } catch (e) { toast(e.message); }
}

/* One row per person, whether skipcast knows them as a voice to cut, a feed
   to follow, or both. The old Speakers and People tabs merged. */
function peopleHtml() {
  const speakers = STATE.speakers || [];
  const persons = PEOPLE || [];
  const bySpeaker = new Map(persons.map(p => [p.speaker, p]));
  const names = [...new Set([...speakers.map(s => s.name),
                             ...persons.map(p => p.speaker)])];
  if (!names.length) {
    return `<div class="empty">No voices named yet.<br>Open an episode and name
      the speakers you hear — named voices can be cut or followed.</div>`;
  }
  const rules = STATE.feed_rules || [];
  return `<div class="card">${names.map(name => {
    const s = speakers.find(x => x.name === name);
    const p = bySpeaker.get(name);
    const exceptions = rules.filter(r => r.speaker === name);
    const open = UI.personOpen === name;
    const ini = name.split(/\s+/).slice(0, 2).map(w => w[0] || '').join('').toUpperCase();
    const status = [];
    if (s && s.skip) status.push('cut from every show'
      + (exceptions.length ? ` · ${exceptions.length} exception${exceptions.length === 1 ? '' : 's'}` : ''));
    else if (exceptions.some(r => r.skip)) status.push('cut on some shows');
    if (p) status.push(`personal feed · ${p.ready_count} appearance${p.ready_count === 1 ? '' : 's'}`);
    if (!status.length) status.push(`${s ? s.profiles : 0} voice sample${s && s.profiles === 1 ? '' : 's'}`);
    return `
      <div class="prow">
        <div class="head" onclick="UI.personOpen=${open ? 'null' : JSON.stringify(name)};render()">
          <div class="pava">${esc(ini)}</div>
          <div class="grow"><div class="t">${esc(name)}</div>
          <div class="s">${esc(status.join(' · '))}</div></div>
          ${s && s.skip ? '<span class="cutpill">cut</span>'
                        : '<span class="keeppill">kept</span>'}
        </div>
        ${open ? personBodyHtml(name, s, p, exceptions) : ''}
      </div>`;
  }).join('')}</div>`;
}

function personBodyHtml(name, s, p, exceptions) {
  const n = JSON.stringify(name);
  return `<div class="pbody stack">
    ${s ? `
    <div class="row">
      <div class="grow"><div style="font-size:13.5px">Cut from every episode</div>
        <div class="s">Exceptions per show live on each show's page.</div></div>
      <label class="switch">
        <input type="checkbox" ${s.skip ? 'checked' : ''}
               onchange='setSkip(${n}, this.checked)'>
        <span class="slider"></span>
      </label>
    </div>
    ${exceptions.length ? `<div class="s">${exceptions.map(r =>
      `${r.skip ? 'cut on' : 'kept on'} ${esc(feedTitle(r.slug))}`).join(' · ')}</div>` : ''}
    ` : ''}
    ${p ? `
      <div class="s">Their feed — every appearance across your shows, everyone
        else edited out:</div>
      <code>${esc(STATE.base_url)}/persons/${esc(p.slug)}.xml</code>
      <div class="wrap">
        <button class="btn" onclick='copy(${JSON.stringify(
          (STATE.base_url || '') + '/persons/' + p.slug + '.xml')})'>Copy link</button>
        <button class="btn" onclick="buildPerson('${esc(p.slug)}')">Rebuild</button>
        <button class="btn danger" onclick="removePerson('${esc(p.slug)}')">Remove feed</button>
      </div>`
    : (s ? `<button class="btn" onclick='addPersonByName(${n})'>
        Make a feed of them</button>` : '')}
  </div>`;
}

async function setSkip(name, skip) {
  try {
    await api(`/api/speakers/${encodeURIComponent(name)}/skip`, {method:'POST', body:{skip}});
    toast(skip ? `${name} will be cut — already-fetched episodes need a re-cut`
               : `${name} will be kept`);
    refresh(true);
  } catch (e) { toast(e.message); }
}

async function addPersonByName(name) {
  try {
    const r = await api('/api/persons', {method:'POST', body:{name}});
    toast(`Following ${r.speaker}`);
    await loadPeople();
    buildPerson(r.slug);
  } catch (e) { toast(e.message); }
}
async function buildPerson(slug) {
  try {
    await api(`/api/persons/${slug}/build`, {method:'POST', body:{}});
    toast('Building — the feed updates here when it finishes');
    await refresh(true);
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

/* ---- add --------------------------------------------------------------- */
function openAdd() { setHash('#/add'); }

function renderAdd() {
  el.innerHTML = `
    <div class="backrow"><span class="b" onclick="goBack('#/library')">‹ Library</span></div>
    <div class="card pad">
      <div class="sub">This tells <b>skipcast</b> which shows to download and cut.
        It does not subscribe your phone — for that, open the show here once it
        is added and use the feed link.</div>
    </div>
    <div class="card pad">
      <div class="sec" style="margin-top:0"><h4 style="font-size:16px">
        ${STATE.search_enabled ? 'Search for a show' : 'Add by RSS URL'}</h4></div>
      <div class="stack">
        ${STATE.search_enabled ? `
        <input type="search" id="aq" placeholder="Podcast name…"
               autocapitalize="none" autocorrect="off">
        <button class="btn primary" onclick="doPodSearch()">Search</button>` : ''}
        <div id="aresults"></div>
      </div>
    </div>
    <div class="card pad">
      <div class="sec" style="margin-top:0"><h4 style="font-size:16px">Add by RSS URL</h4></div>
      <div class="stack">
        <input type="text" id="aurl" placeholder="https://…/feed.xml"
               autocapitalize="none" autocorrect="off" inputmode="url">
        <button class="btn" onclick="addUrl(this)">Subscribe</button>
      </div>
    </div>`;
  const q = document.getElementById('aq');
  if (q) q.addEventListener('keydown', e => { if (e.key === 'Enter') doPodSearch(); });
}

async function doPodSearch() {
  const field = document.getElementById('aq');
  const q = (field ? field.value : '').trim();
  if (!q) { toast('Type a podcast name first'); if (field) field.focus(); return; }
  const box = document.getElementById('aresults');
  box.innerHTML = '<div class="empty"><span class="spin"></span> Searching…</div>';
  try {
    const { results } = await api('/api/search?q=' + encodeURIComponent(q));
    if (!results.length) { box.innerHTML = '<div class="empty">Nothing found.</div>'; return; }
    box.innerHTML = results.map(r => `
      <div class="item row">
        ${r.artwork ? `<img class="podart" src="${esc(r.artwork)}" alt="">`
                    : '<div class="podart"></div>'}
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
  const field = document.getElementById('aurl');
  const u = (field ? field.value : '').trim();
  if (!u) { toast('Paste a feed URL first'); if (field) field.focus(); return; }
  if (!/^https?:\/\//i.test(u)) { toast('That needs to start with http:// or https://'); return; }
  subscribe(u, btn, 'Subscribe');
}

/* ---- show page --------------------------------------------------------- */
function renderFeed(v) {
  const f = v.data.feed, eps = v.data.episodes;
  const feedUrl = `${STATE.base_url}/feeds/${f.slug}.xml`;
  const ready = eps.filter(e => e.status === 'ready');
  // The value proposition, one line: who this show loses and how much time
  // that buys back per episode.
  const cutNames = new Set();
  let cutTotal = 0;
  ready.forEach(e => {
    (e.cut_speakers || '').split(',').forEach(n => { n = n.trim(); if (n) cutNames.add(n); });
    cutTotal += e.cut_seconds || 0;
  });
  const perEp = ready.length ? Math.round(cutTotal / ready.length / 60) : 0;
  const badge = cutNames.size
    ? `✂️ <b>${esc([...cutNames].join(', '))} removed</b>${perEp
        ? ` · saves ~${perEp} min per episode` : ''}`
    : (perEp ? `✂️ <b>ads removed</b> · ~${perEp} min per episode` : '');
  const latest = ready[0];
  el.innerHTML = `
    <div class="backrow">
      <span class="b" onclick="goBack('#/library')">‹ Library</span>
      <span class="dots" onclick="feedMenu('${esc(f.slug)}')">···</span>
    </div>
    <div class="showhero">
      ${artHtml(f.image_url, f.title || f.slug, '', 40).replace('class="art "',
        'class="art" style="width:150px;height:150px;border-radius:18px;margin:0 auto"')}
      <h5>${esc(f.title || f.slug)}</h5>
      <div class="a">${esc(f.author || '')}${f.author ? ' · ' : ''}${eps.length}
        episode${eps.length === 1 ? '' : 's'}</div>
      ${badge ? `<div class="k">${badge}</div>` : ''}
    </div>
    <div class="cta">
      ${latest ? `<button class="btn primary"
        onclick="playEpisode('${esc(latest.key)}')">▶ Play latest</button>` : ''}
      <button class="btn" onclick="pollFeed('${esc(f.slug)}',1)">↻ Fetch</button>
    </div>
    ${segmentSuggestHtml(f)}
    ${skipNoteHtml(eps)}
    <div class="card">
      ${eps.length ? eps.map(e => feedEpRow(e)).join('')
        : '<div class="empty">Nothing fetched yet. Tap ↻ Fetch.</div>'}
    </div>
    <div class="card">
      ${feedRulesHtml(f, eps)}
      <h2 class="fold" onclick="UI.subOpen=!UI.subOpen;render()">
        <span>Listen in your podcast app<span class="s">The feed link for
          Overcast, Apple Podcasts…</span></span>
        <span class="ch">${UI.subOpen ? '▾' : '▸'}</span></h2>
      ${UI.subOpen ? `<div class="foldbody stack">
        <code>${esc(feedUrl)}</code>
        <div class="wrap">
          <button class="btn" onclick='copy(${JSON.stringify(feedUrl)})'>Copy link</button>
          <a class="btn" href="${esc(feedUrl)}">Open</a>
        </div></div>` : ''}
      ${f.description ? `
      <h2 class="fold" onclick="UI.aboutOpen=!UI.aboutOpen;render()">
        <span>About</span><span class="ch">${UI.aboutOpen ? '▾' : '▸'}</span></h2>
      ${UI.aboutOpen ? `<div class="foldbody sub"
        style="font-size:13.5px">${esc(f.description)}</div>` : ''}` : ''}
    </div>`;
}

function feedEpRow(e) {
  const when = dateShort(e.published_ts);
  const inListen = (LISTEN || []).find(x => x.key === e.key);
  const pos = inListen ? (inListen.position || 0) : 0;
  const dur = e.result_seconds || 0;
  const pct = dur && pos > 30 && !((inListen || {}).finished)
    ? Math.min(100, pos / dur * 100) : 0;
  const play = e.status === 'ready'
    ? `<button class="pbtn ${NOW && NOW.key === e.key && !audio.paused ? 'on' : ''}"
        onclick="event.stopPropagation();playEpisode('${esc(e.key)}')">▶ ${
          pct ? mins(Math.max(0, dur - pos)) + ' left' : mins(dur)}</button>`
    : `<span class="pill ${esc(e.status)}">${esc(e.status)}</span>`;
  return `
    <div class="eprow" onclick="openEpisode('${esc(e.key)}')">
      <div class="grow">
        ${when ? `<div class="s" style="margin-bottom:2px; text-transform:uppercase">${when}</div>` : ''}
        <div class="t">${esc(e.title)}</div>
        <div class="s">${e.status === 'ready'
          ? (removedLabel(e) || mins(e.result_seconds))
          : esc((e.error || '').slice(0, 90))}</div>
        ${pct ? `<div class="prog" style="width:120px"><i style="width:${pct}%"></i></div>` : ''}
      </div>
      ${play}
    </div>`;
}

function feedMenu(slug) {
  openSheet([
    {label: 'Fetch newest 3', fn: () => pollFeed(slug, 3)},
    {label: 'Copy feed link', fn: () => copy(`${STATE.base_url}/feeds/${slug}.xml`)},
    {label: 'Unsubscribe from this show', danger: true, fn: () => unsub(slug)},
  ]);
}

/* Three states, not a toggle: a speaker either follows their global flag or
   this show overrides it in one direction. Only voices with a footprint on
   this show appear by default — a rule for a host who never shows up is
   noise; the rest hide behind Show all. */
/* Segments this show runs most weeks, offered as one-tap skips. Only shown
   for terms with no opinion recorded yet, so a suggestion never argues with a
   decision already made. */
let SEGMENTS = {};

async function loadSegments(slug) {
  try { SEGMENTS[slug] = (await api(`/api/feeds/${slug}/segments`)).segments; }
  catch (e) { SEGMENTS[slug] = []; }
  if (VIEW && VIEW.kind === 'feed' && VIEW.slug === slug) render();
}

function segmentSuggestHtml(f) {
  const segs = SEGMENTS[f.slug];
  if (segs === undefined) { loadSegments(f.slug); return ''; }
  const s = segs.find(x => !UI.dismissedSegments.includes(x.term));
  if (!s) return '';
  return `
    <div class="suggest">
      <div class="tag">Recurring segment spotted</div>
      <div class="t" style="font-size:13.5px">“${esc(s.term)}” appears in
        ${s.episodes} of ${s.of_episodes} episodes${s.avg_seconds
          ? ` (about ${mins(s.avg_seconds)} each)` : ''}</div>
      <div class="s" style="margin-top:3px">${esc(s.examples[0] || '')}</div>
      <div class="wrap" style="margin-top:10px">
        <button class="btn primary" onclick='setTermState(${JSON.stringify(s.term)}, "skip")'>
          Skip ${esc(s.term)}</button>
        <button class="btn" onclick='dismissSegment(${JSON.stringify(s.term)})'>Keep it</button>
      </div>
    </div>`;
}

function dismissSegment(term) {
  UI.dismissedSegments.push(term);
  render();
}

/* An episode that is mostly about something being skipped was left whole on
   purpose. Saying so — and offering both answers — beats either hollowing it
   out or pretending the skip did not apply. */
function skipNoteHtml(eps) {
  const held = (eps || []).filter(e => e.skip_note);
  if (!held.length) return '';
  return held.map(e => `
    <div class="warncard">
      <div class="t" style="font-size:13.5px">⚠️ Left whole: ${esc(e.title)}</div>
      <div class="s" style="margin-top:3px">${esc(e.skip_note)}. Nothing was cut
        from it — cutting that much would leave an episode not worth playing.</div>
      <div class="wrap" style="margin-top:10px">
        <button class="btn" onclick="keepEpisode('${esc(e.key)}')">Listen anyway</button>
        <button class="btn danger" onclick="hideEpisode('${esc(e.key)}')">Drop this episode</button>
      </div>
    </div>`).join('');
}

async function keepEpisode(key) {
  try {
    await api(`/api/episodes/${key}/keep`, {method:'POST'});
    toast('Kept — it stays whole in Listen');
    reloadFeed(VIEW && VIEW.slug);
  } catch (e) { toast(e.message); }
}

async function hideEpisode(key) {
  try {
    await api(`/api/episodes/${key}/hide`, {method:'POST', body:{hidden:true}});
    toast('Dropped from Listen — the files stay on disk');
    await refresh(true);
    reloadFeed(VIEW && VIEW.slug);
  } catch (e) { toast(e.message); }
}

function feedRulesHtml(f, eps) {
  const rules = STATE.feed_rules || [];
  const heard = new Set();
  (eps || []).forEach(e => (e.cut_speakers || '').split(',')
    .forEach(n => { n = n.trim(); if (n) heard.add(n); }));
  const relevant = STATE.speakers.filter(s => heard.has(s.name)
    || rules.some(r => r.slug === f.slug && r.speaker === s.name));
  const shown = UI.rulesAll ? STATE.speakers : relevant;
  const overrides = rules.filter(r => r.slug === f.slug).length;
  const rows = shown.map(s => {
    const rule = rules.find(r => r.slug === f.slug && r.speaker === s.name);
    const mode = rule ? (rule.skip ? 'cut' : 'keep') : 'default';
    const on = m => m === mode ? 'primary' : '';
    const n = JSON.stringify(s.name), slug = JSON.stringify(f.slug);
    return `
      <div class="item">
        <div class="title">${esc(s.name)}</div>
        <div class="sub">${mode === 'default'
          ? (s.skip ? 'cut from every show' : 'kept everywhere')
          : (mode === 'cut' ? 'cut from this show'
             : 'kept here' + (s.skip ? ', despite being cut elsewhere' : ''))}</div>
        <div class="wrap" style="margin-top:8px">
          <button class="btn ${on('default')}" onclick='setRule(${slug},${n},null)'>Default</button>
          <button class="btn ${on('cut')}" onclick='setRule(${slug},${n},true)'>Cut</button>
          <button class="btn ${on('keep')}" onclick='setRule(${slug},${n},false)'>Keep</button>
        </div>
      </div>`;
  }).join('');

  // Topics get the same three states as people, because from this page they
  // are the same question: what does this show lose?
  const topicRules = (TOPICS && TOPICS.rules) || [];
  const topicRows = ((TOPICS && TOPICS.topics) || []).map(t => {
    const norm = t.term.toLowerCase();
    const rule = topicRules.find(r => r.slug === f.slug && r.term_norm === norm);
    const mode = rule ? (rule.skip ? 'cut' : 'keep') : 'default';
    const on = m => m === mode ? 'primary' : '';
    const n = JSON.stringify(t.term), slug = JSON.stringify(f.slug);
    return `
      <div class="item">
        <div class="title">${esc(t.term)}
          <span class="sub" style="font-weight:400">topic</span></div>
        <div class="sub">${mode === 'default'
          ? (t.state === 'skip' ? 'skipped everywhere' : 'kept everywhere')
          : (mode === 'cut' ? 'skipped on this show'
             : 'kept here' + (t.state === 'skip' ? ', despite being skipped elsewhere' : ''))}</div>
        <div class="wrap" style="margin-top:8px">
          <button class="btn ${on('default')}" onclick='setTopicRule(${slug},${n},null)'>Default</button>
          <button class="btn ${on('cut')}" onclick='setTopicRule(${slug},${n},true)'>Skip</button>
          <button class="btn ${on('keep')}" onclick='setTopicRule(${slug},${n},false)'>Keep</button>
        </div>
      </div>`;
  }).join('');

  const hidden = STATE.speakers.length - shown.length;
  const total = overrides + topicRules.filter(r => r.slug === f.slug).length;
  if (!rows && !topicRows) return '';
  return `
    <h2 class="fold" onclick="UI.rulesOpen=!UI.rulesOpen;render()">
      <span>What gets cut from this show<span class="s">${total
        ? `${total} exception${total === 1 ? '' : 's'} set`
        : 'Exceptions to the global switches'}</span></span>
      <span class="ch">${UI.rulesOpen ? '▾' : '▸'}</span></h2>
    ${UI.rulesOpen ? `<div class="foldbody">
      <div class="sub" style="margin-bottom:10px">Default follows the switches in
        Library. Set one here to make this show an exception.
        Episodes already fetched are re-cut automatically.</div>
      ${rows}${topicRows}
      ${!rows && !topicRows
        ? '<div class="sub">Nothing known is heard on this show yet.</div>' : ''}
      ${hidden > 0 ? `<button class="btn" style="margin-top:11px"
        onclick="UI.rulesAll=true;render()">Show all ${STATE.speakers.length} voices</button>` : ''}
    </div>` : ''}`;
}

async function setTopicRule(slug, term, skip) {
  try {
    const r = await api(`/api/feeds/${slug}/topic-rules`,
                        {method:'POST', body:{term, skip}});
    toast(skip === null ? `${term} follows the global setting here`
          : skip ? `${term} will be skipped on this show`
                 : `${term} will be kept on this show`
          + (r.recutting ? ` — re-cutting ${r.recutting}` : ''));
    await loadTopics();
    await refresh(true);
    render();
  } catch (e) { toast(e.message); }
}

async function setRule(slug, name, skip) {
  try {
    await api(`/api/feeds/${slug}/rules`, {method:'POST', body:{name, skip}});
    toast(skip === null ? `${name} follows the global setting here`
          : skip ? `${name} will be cut from this show`
                 : `${name} will be kept on this show`);
    await refresh(true);
    render();
  } catch (e) { toast(e.message); }
}

async function pollFeed(slug, limit) {
  try {
    await api(`/api/feeds/${slug}/poll`, {method:'POST', body:{limit}});
    toast('Fetching — each episode takes about 15 minutes and will appear here');
    await refresh(true);
  } catch (e) { toast(e.message); }
}

async function unsub(slug) {
  if (!confirm('Unsubscribe? Downloaded files stay on disk.')) return;
  try {
    await api(`/api/feeds/${slug}`, {method:'DELETE'});
    toast('Unsubscribed'); setHash('#/library'); refresh();
  } catch (e) { toast(e.message); }
}

/* ---- episode page ------------------------------------------------------ */
function renderEpisode(v) {
  const e = v.data;
  el.innerHTML = `
    <div class="backrow">
      <span class="b" onclick="goBack('#/feed/${esc(e.feed_slug || '')}')">‹ ${
        esc(e.feed_title || e.feed_slug || 'Back')}</span>
      <span class="dots" onclick="episodeMenu('${esc(e.key)}')">···</span>
    </div>
    <div class="ephero">
      ${artHtml(feedArt(e.feed_slug), e.feed_title || e.feed_slug, '', 26)
        .replace('class="art "', 'class="art" style="width:92px;height:92px;border-radius:14px"')}
      <div>
        <h5>${esc(e.title)}</h5>
        <div class="m">${dateShort(e.published_ts)}${
          e.index && e.index.kind_label ? ' · ' + esc(e.index.kind_label) : ''}</div>
      </div>
    </div>
    ${e.status === 'ready' ? `
      <button class="bigplay" id="ep-play"
        onclick="playEpisode('${esc(e.key)}')">${playLabel(e.key)}</button>
      <div class="savings">${mins(e.original_seconds)} → ${mins(e.result_seconds)}${
        removedLabel(e) ? ' · ✂️ ' + removedLabel(e) : ''}</div>`
      : `<div class="savings" style="margin-top:14px">${esc(e.error || e.status)}</div>`}
    ${chaptersHtml(e)}
    ${detailsHtml(e)}
    <div class="card">
      ${summaryFoldHtml(e)}
      ${adsFoldHtml(e)}
      ${voicesFoldHtml(e)}
    </div>`;
}

function episodeMenu(key) {
  const e = VIEW && VIEW.data;
  const items = [];
  if (e && e.has_transcript) items.push({label: 'Read transcript',
    fn: () => window.open(`/api/episodes/${key}/transcript`, '_blank')});
  if (STATE.summary_ready) items.push({label: e && e.summary
    ? 'Summarise again' : 'Summarise this episode',
    fn: () => epJob(key, 'summarize')});
  items.push({label: 'Re-cut with current settings', fn: () => epJob(key, 'recut')});
  items.push({label: 'Reprocess from scratch', danger: true,
    fn: () => epJob(key, 'reprocess')});
  openSheet(items);
}

/* Chapters, including the ones that are gone. A skipped chapter stays on the
   list struck through, says which term took it, and offers the original —
   the same contract the ad audit makes. Cutting something and then hiding
   that you cut it is how a listener stops trusting the edit. */
function chaptersHtml(e) {
  const idx = e.index;
  if (!idx || !idx.topics.length) return '';
  return `
    <div class="sec"><h4 style="font-size:16px">Chapters</h4>
      <span class="hint">from the summary</span></div>
    <div class="card">
      ${idx.topics.map((t, i) => {
        const skipped = (t.skipped_by || []).length;
        return `
        <div class="chapter ${skipped ? 'ghost' : ''}">
          <div class="no">${i + 1}</div>
          <div class="t"><span class="ttl">${esc(t.title)}</span>
            ${skipped ? `<span class="why">skipped ·
              <b>${t.skipped_by.map(esc).join(', ')}</b>${
                t.at_seconds != null ? ' · ' + clock(t.at_seconds) + ' in the original' : ''}</span>` : ''}
            ${(() => {
              /* Same story elsewhere this week — worth knowing before you
                 listen to it a second time. */
              const rel = (e.related || {})[String(i)] || [];
              if (!rel.length) return '';
              const names = [...new Set(rel.map(r => r.feed_title || r.feed_slug))];
              return `<span class="rel">also covered by ${names.map(esc).join(', ')}
                <a class="act" onclick="openEpisode('${esc(rel[0].episode_key)}')">open</a></span>`;
            })()}
          </div>
          ${skipped
            ? `<button class="pbtn" onclick="play(this,'${esc(e.key)}',${t.at_seconds},${t.at_seconds + 25})">▶ check</button>`
            : (t.at_cut != null ? `<span class="at"
                onclick="playEpisode('${esc(e.key)}',${t.at_cut})">▶ ${clock(t.at_cut)}</span>` : '')}
          <span class="dots" style="font-size:17px"
            onclick="chapterMenu('${esc(e.key)}',${i})">⋯</span>
        </div>`;
      }).join('')}
      ${idx.topics.some(t => (t.skipped_by || []).length) ? `
        <div class="sub" style="padding:8px 0 4px">Tap check to hear a skipped
          chapter from the retained original. Wrong? ⋯ → stop skipping it.</div>` : ''}
    </div>`;
}

/* The zero-typing way in: you are looking at a chapter you never listen to,
   and the term to skip is sitting in its title. */
function chapterMenu(key, i) {
  const e = VIEW && VIEW.data;
  if (!e || !e.index) return;
  const t = e.index.topics[i];
  const skipped = (t.skipped_by || []).length;
  const suggestion = chapterTerm(t.title);
  const items = [];
  if (!skipped && t.at_cut != null) {
    items.push({label: `▶ Play from ${clock(t.at_cut)}`,
                fn: () => playEpisode(key, t.at_cut)});
  }
  if (skipped) {
    t.skipped_by.forEach(term => items.push({
      label: `Stop skipping “${term}”`,
      fn: () => applyTermState(term, null),
    }));
  } else if (suggestion) {
    items.push({label: `Skip chapters like this — “${suggestion}”`,
                fn: () => setTermState(suggestion, 'skip')});
    items.push({label: `Watch “${suggestion}” instead`,
                fn: () => applyTermState(suggestion, 'watch')});
  }
  if (items.length) openSheet(items);
}

/* What to offer skipping, taken from a chapter title: its leading significant
   words, which is where shows put a segment's name. Long enough to be
   specific, short enough to still match next week's episode. */
const CHAPTER_STOP = new Set(['the','a','an','and','or','but','of','in','on',
  'for','to','is','are','was','were','be','with','at','by','from','as','it',
  'its','this','that','these','those','vs','versus','about','into','over',
  'new','why','how','what','when','who','more','than','up','down']);

function chapterTerm(title) {
  const words = (title || '').split(/[^\p{L}\p{N}]+/u)
    .filter(w => w.length > 2 && !CHAPTER_STOP.has(w.toLowerCase()));
  return words.slice(0, 2).join(' ');
}

/* What was offered for a claim in the episode — not a verdict on whether the
   claim is true. Coloured by how much weight it carries. */
const EVIDENCE_COLOUR = {trial: 'var(--ok)', observational: 'var(--ok)',
                         mechanism: 'var(--warn)', anecdote: 'var(--warn)',
                         authority: 'var(--warn)', none: 'var(--bad)'};
function evidencePill(ev) {
  if (!ev) return '';
  const label = ev === 'none' ? 'asserted' : ev;
  return `<span class="pill" style="color:${EVIDENCE_COLOUR[ev] || 'var(--muted)'}"
    title="what the episode offered for this claim">${esc(label)}</span>`;
}

/* Key details render twice at different weights: a swipeable chip strip by
   default, the full annotated list on demand. */
function detailsHtml(e) {
  const idx = e.index;
  if (!idx || !idx.specifics.length) return '';
  const jump = s => {
    if (s.at_cut == null) return '';
    if (!s.removed) return `<span class="at"
      onclick="playEpisode('${esc(e.key)}',${s.at_cut})">▶ ${clock(s.at_cut)}</span>`;
    return `<span class="at"
      onclick="play(this,'${esc(e.key)}',${s.at_seconds},${s.at_seconds + 25})">▶ in the original</span>`;
  };
  if (!UI.detailsAll) {
    return `
      <div class="sec"><h4 style="font-size:16px">Key details</h4>
        <a onclick="UI.detailsAll=true;render()">All ${idx.specifics.length}</a></div>
      <div class="kchips">
        ${idx.specifics.slice(0, 8).map(s => `
          <div class="kchip" onclick="UI.detailsAll=true;render()">
            <div class="v">${esc(s.value)}</div>
            ${s.detail ? `<div class="d">${esc(s.detail)}</div>` : ''}
          </div>`).join('')}
      </div>`;
  }
  return `
    <div class="sec"><h4 style="font-size:16px">Key details</h4>
      <a onclick="UI.detailsAll=false;render()">Collapse</a></div>
    <div class="card">
      ${idx.specifics.map(s => `
        <div class="hit">
          <div class="q" style="font-weight:650">${esc(s.value)}
            <span class="pill">${esc(s.type)}</span>
            ${s.confidence && s.confidence !== 'firm'
              ? `<span class="pill" style="color:var(--warn)">${esc(s.confidence)}</span>` : ''}
            ${evidencePill(s.evidence)}</div>
          ${s.detail ? `<div class="m">${esc(s.detail)}</div>` : ''}
          <div class="m">${s.speaker ? '— ' + esc(s.speaker) + ' · ' : ''}${jump(s)}</div>
        </div>`).join('')}
    </div>`;
}

function summaryFoldHtml(e) {
  if (!e.summary) {
    return `
      <h2 class="fold" onclick="UI.summaryOpen=!UI.summaryOpen;render()">
        <span>Summary<span class="s">Not summarised yet${
          e.has_transcript ? ' — transcript is ready' : ''}</span></span>
        <span class="ch">${UI.summaryOpen ? '▾' : '▸'}</span></h2>
      ${UI.summaryOpen ? `<div class="foldbody stack">
        <button class="btn primary" onclick="epJob('${esc(e.key)}','summarize')"
          ${STATE.summary_ready ? '' : 'disabled'}>Summarise this episode</button>
        <div class="sub">${
          !STATE.summary_enabled
            ? 'Summaries are switched off in config.toml.'
            : STATE.summary_ready
              ? `Transcribes locally, then summarises with ${esc(STATE.summary_provider)}. Takes about 20 minutes.`
              : `No API key for ${esc(STATE.summary_provider)}. Add it to the .env file beside config.toml, then restart the service — it only reads the file at startup.`
        }</div></div>` : ''}`;
  }
  return `
    <h2 class="fold" onclick="UI.summaryOpen=!UI.summaryOpen;render()">
      <span>Full summary<span class="s">The complete write-up${
        e.has_transcript ? ' and transcript' : ''}</span></span>
      <span class="ch">${UI.summaryOpen ? '▾' : '▸'}</span></h2>
    ${UI.summaryOpen ? `<div class="foldbody">
      <div class="md">${md(e.summary)}</div>
      <div class="wrap" style="margin-top:11px">
        ${e.has_transcript ? `<a class="btn"
          href="/api/episodes/${esc(e.key)}/transcript" target="_blank">Read transcript</a>` : ''}
      </div>
      ${e.index ? '' : `<div class="sub" style="margin-top:9px">This summary
        predates topic links. Summarising again (··· menu) adds them — it
        reuses the transcript, so it takes about a minute.</div>`}
    </div>` : ''}`;
}

/* What was removed as advertising, listed so it can be checked. A feature
   that deletes part of an episode has to show its work. */
function adsFoldHtml(e) {
  const idx = e.index;
  const cuts = idx && (idx.interstitials || []);
  if (!cuts || !cuts.length) return '';
  const total = Math.round(cuts.reduce((a, i) => a + i.seconds, 0) / 60);
  return `
    <h2 class="fold" onclick="UI.adsOpen=!UI.adsOpen;render()">
      <span>Removed as ads<span class="s">${cuts.length} segment${
        cuts.length === 1 ? '' : 's'} · ${total}m — tap to audit</span></span>
      <span class="ch">${UI.adsOpen ? '▾' : '▸'}</span></h2>
    ${UI.adsOpen ? `<div class="foldbody">
      ${cuts.map(i => `
        <div class="item">
          <div class="row">
            <div class="grow">
              <div class="title wrapline" style="font-size:14px">${esc(i.what || i.kind)}
                <span class="pill">${esc(i.kind)}</span>
                ${i.confidence !== 'certain'
                  ? `<span class="pill" style="color:var(--warn)">${esc(i.confidence)}</span>` : ''}
              </div>
              <div class="sub">${esc(i.from)}–${esc(i.to)} · ${Math.round(i.seconds)}s</div>
            </div>
            <button class="pbtn"
              onclick="play(this,'${esc(e.key)}',${i.from_seconds},${i.from_seconds + 20})">
              ▶ check</button>
          </div>
        </div>`).join('')}
      <div class="sub" style="margin-top:9px">Tap check to hear what was cut, from
        the retained original. Wrong? Set
        <code>enabled = false</code> under <code>[interstitial]</code>, then re-cut.</div>
    </div>` : ''}`;
}

/* One cluster with the full toolkit: listen, then name or correct. */
function clusterHtml(e, c) {
  return `
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
        ${c.samples.map(s => `<button class="pbtn"
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
    </div>`;
}

/* Recognised voices need a glance, not a toolkit; unnamed voices with real
   airtime are the actual task; sub-30-second fragments are diarization
   noise. Each tier gets the weight it deserves. */
function voicesFoldHtml(e) {
  if (!e.clusters || !e.clusters.length) return '';
  const known = STATE.speakers.map(s => `<option value="${esc(s.name)}">`).join('');
  const named = e.clusters.filter(c => c.matched_name);
  const unnamed = e.clusters.filter(c => !c.matched_name && c.has_embedding
                                         && c.total_seconds >= 30);
  const noise = e.clusters.length - named.length - unnamed.length;
  const names = [...new Set(named.map(c => c.matched_name))];
  const sub = [names.length ? `${names.length} recognised` : null,
               unnamed.length ? `${unnamed.length} unnamed` : null]
    .filter(Boolean).join(' · ') || 'not identified yet';
  let body;
  if (UI.clustersAll) {
    body = `<div class="sub" style="margin-bottom:10px">Tap ▶ to hear a voice,
        then name it. Named voices are recognised in every future episode.
        <a class="act" onclick="UI.clustersAll=false;render()">Fold</a></div>
      ${e.clusters.map(c => clusterHtml(e, c)).join('')}`;
  } else {
    // Diarization can split one person into several clusters; one row per name.
    const byName = new Map();
    named.forEach(c => {
      const cur = byName.get(c.matched_name);
      if (cur) cur.total_seconds += c.total_seconds;
      else byName.set(c.matched_name, {name: c.matched_name, color: c.color,
                                       skip: c.skip, total_seconds: c.total_seconds});
    });
    body = `${[...byName.values()].map(p => `
        <div class="item row">
          <div class="swatch" style="background:${esc(p.color)}"></div>
          <div class="grow">
            <div class="title">${esc(p.name)}
              ${p.skip ? '<span class="pill" style="color:var(--bad)">cut</span>' : ''}</div>
            <div class="sub">${mins(p.total_seconds)}</div>
          </div>
        </div>`).join('')}
      ${unnamed.length ? `
        <div class="sub" style="margin:11px 0 4px">Unrecognised — tap ▶ to hear,
          then name. Named voices are recognised in every future episode.</div>
        ${unnamed.map(c => clusterHtml(e, c)).join('')}` : ''}
      <button class="btn" style="margin-top:11px" onclick="UI.clustersAll=true;render()">
        Edit voices${noise > 0 ? ` · ${noise} short clip${noise === 1 ? '' : 's'} hidden` : ''}</button>`;
  }
  return `
    <h2 class="fold" onclick="UI.voicesOpen=!UI.voicesOpen;render()">
      <span>Voices<span class="s">${esc(sub)}</span></span>
      <span class="ch">${UI.voicesOpen ? '▾' : '▸'}</span></h2>
    ${UI.voicesOpen ? `<div class="foldbody">${body}
      <datalist id="known">${known}</datalist></div>` : ''}`;
}

async function nameIt(key, cluster) {
  const name = document.getElementById('n-' + cluster).value.trim();
  if (!name) return toast('Enter a name first');
  try {
    await api(`/api/episodes/${key}/label`, {method:'POST',
      body:{cluster_label: cluster, name}});
    toast(`Saved ${name}`);
    await refresh(true);
    reloadEpisode(key);
  } catch (e) { toast(e.message); }
}

const EPJOB_TOAST = {
  recut: 'Re-cutting — about a minute; this page updates when it lands',
  reprocess: 'Reprocessing from scratch — roughly 15 minutes',
  summarize: 'Summarising — this page updates when it lands',
};
async function epJob(key, action) {
  try {
    await api(`/api/episodes/${key}/${action}`, {method:'POST'});
    toast(EPJOB_TOAST[action] || 'Started');
    await refresh(true);
  } catch (e) { toast(e.message); }
}

/* ---- activity ---------------------------------------------------------- */
function renderJobs() {
  el.innerHTML = `
    <div class="bigtitle"><h2>Activity</h2></div>
    <div class="card">
    ${STATE.jobs.length ? STATE.jobs.map(j => `
      <div class="eprow" onclick="openJob(${j.id})">
        <div class="grow">
          <div class="t">${esc(j.label || j.kind)}</div>
          <div class="s">${esc((j.progress || j.error || '').slice(0,80))}</div>
        </div>
        <span class="pill ${esc(j.status)}">${j.status === 'running'
          ? '<span class="spin"></span> running' : esc(j.status)}</span>
      </div>`).join('')
    : '<div class="empty">Nothing has run yet.</div>'}</div>`;
}

async function loadJobView(id) {
  VIEW = {kind:'job', id, data:null};
  try { VIEW.data = await api('/api/jobs/' + id); } catch (e) { toast(e.message); }
  render();
}

function renderJob(v) {
  const j = v.data || {};
  el.innerHTML = `
    <div class="backrow"><span class="b" onclick="goBack('#/jobs')">‹ Activity</span></div>
    <div class="card pad">
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
    <div class="card pad"><pre id="log">${esc(j.log || '(nothing yet)')}</pre></div>`;
  const pre = document.getElementById('log');
  if (pre) pre.scrollTop = pre.scrollHeight;
  if (j.status === 'running' || j.status === 'queued') {
    setTimeout(() => { if (VIEW && VIEW.kind === 'job' && VIEW.id === j.id) loadJobView(j.id); }, 3000);
  }
}

async function cancelJob(id) {
  const r = await api(`/api/jobs/${id}/cancel`, {method:'POST'});
  toast(r.ok ? 'Cancelled' : r.note); refresh(true); loadJobView(id);
}

/* ---- view loaders ------------------------------------------------------ */
function setHash(h) {
  if (location.hash === h) route();
  else location.hash = h;
}
function goBack(parent) {
  if (HIST > 1) history.back();
  else setHash(parent);  // deep link — there is no in-app history to pop
}
function openFeed(slug) { setHash('#/feed/' + slug); }
function openEpisode(key) { setHash('#/episode/' + key); }
function openJob(id) { setHash('#/job/' + id); }

async function loadFeedView(slug) {
  UI.subOpen = false; UI.rulesOpen = false; UI.rulesAll = false; UI.aboutOpen = false;
  if (TOPICS === null) loadTopics();
  VIEW = {kind:'feed', slug, data:null};
  el.innerHTML = '<div class="empty"><span class="spin"></span></div>';
  try { VIEW.data = await api(`/api/feeds/${slug}/episodes`); }
  catch (e) { toast(e.message); VIEW = null; return render(); }
  render();
  restoreScroll();
}

/* Refresh a view's data without the spinner — used when a job finishes while
   the user is looking at it. */
async function reloadFeed(slug) {
  try {
    const d = await api(`/api/feeds/${slug}/episodes`);
    if (VIEW && VIEW.kind === 'feed' && VIEW.slug === slug) { VIEW.data = d; render(); }
  } catch (e) {}
}

async function loadEpisodeView(key) {
  UI.summaryOpen = false; UI.clustersAll = false; UI.adsOpen = false;
  UI.voicesOpen = false; UI.detailsAll = false;
  VIEW = {kind:'episode', key, data:null};
  el.innerHTML = '<div class="empty"><span class="spin"></span></div>';
  try { VIEW.data = await api(`/api/episodes/${key}`); }
  catch (e) { toast(e.message); VIEW = null; return render(); }
  render();
  restoreScroll();
}

async function reloadEpisode(key) {
  try {
    const d = await api(`/api/episodes/${key}`);
    if (VIEW && VIEW.kind === 'episode' && VIEW.key === key) { VIEW.data = d; render(); }
  } catch (e) {}
}

/* ---- clips ------------------------------------------------------------- */
/* A clip is saved by reaching backwards from where playback is: you only know
   a passage was worth keeping once it has been said. The server converts the
   player's position into the original episode's clock and works out which
   stretches of it the listener actually heard, so nothing that was cut out of
   the edit can reappear inside a clip. */
let CLIPS = null;
const CLIPUI = {open: null, segs: {}, sel: {}, expanded: {}};

/* Roughly the length that overflows the clamp; only past this is there
   anything hidden worth offering to unfold. */
const QUOTE_CLAMP = 260;
function toggleQuote(key) {
  CLIPUI.expanded[key] = !CLIPUI.expanded[key];
  render();
}

async function saveClip() {
  if (!NOW) return;
  if (NOW.digest) return toast('Clips are saved from episodes, not briefs');
  const btn = document.getElementById('np-clip');
  if (btn) { btn.disabled = true; }
  try {
    const h = await api('/api/highlights', {method: 'POST',
      body: {episode_key: NOW.key, position: audio.currentTime || 0}});
    CLIPS = null;
    if (btn) { btn.classList.add('saved'); setTimeout(() => btn.classList.remove('saved'), 1200); }
    // A clip saved before the transcript lands is a range with no words yet;
    // say so rather than showing an empty quote and looking broken.
    toast(h.quote ? `Saved ${Math.round(h.seconds)}s` : 'Saved — words follow once transcribed');
    if (TAB === 'clips' && !VIEW) render();
  } catch (e) { toast(e.message); }
  finally { if (btn) btn.disabled = false; }
}

async function loadClips() {
  try { CLIPS = (await api('/api/highlights')).highlights; }
  catch (e) { CLIPS = []; toast(e.message); }
  if (TAB === 'clips' && !VIEW) render();
}

function playClip(btn, key) {
  // Shares the one <audio> with samples, so it clears whatever was playing.
  // The first play renders the file server-side, which is why it can take a
  // moment on a clip that has never been played.
  if (playingBtn) playingBtn.classList.remove('on');
  if (playingBtn === btn && !audio.paused) { audio.pause(); playingBtn = null; return; }
  savePosition(true);
  NOW = null; NPCH = null; stopAt = null; syncMini();
  audio.src = `/highlights/${key}.mp3`;
  audio.playbackRate = RATE;
  audio.play().catch(() => toast('Playback blocked — tap again'));
  playingBtn = btn; btn.classList.add('on');
}

function clipCitation(c) {
  const bits = [c.speaker_name, c.feed_title,
                c.published_ts ? dateShort(c.published_ts) : ''].filter(Boolean);
  return `${c.quote || '(no transcript yet)'}\n\n— ${bits.join(', ')}\n${c.episode_title || ''}`.trim();
}

function shareClip(key) {
  const c = (CLIPS || []).find(x => x.key === key);
  if (!c) return;
  const url = `${location.origin}/highlights/${key}.mp3`;
  const text = clipCitation(c);
  const items = [
    {label: 'Copy quote', fn: () => copy(text)},
    {label: 'Copy audio link', fn: () => copy(url)},
    {label: 'Download audio', fn: () => { window.location.href = url; }},
    {label: 'Delete clip', danger: true, fn: () => removeClip(key)},
  ];
  if (navigator.share) items.unshift({label: 'Share…', fn: () =>
    navigator.share({title: c.episode_title || 'Clip', text, url}).catch(() => {})});
  openSheet(items);
}

async function removeClip(key) {
  try {
    await api('/api/highlights/' + key, {method: 'DELETE'});
    CLIPS = null; CLIPUI.open = null; toast('Deleted'); render();
  } catch (e) { toast(e.message); }
}

async function toggleTrim(key) {
  if (CLIPUI.open === key) { CLIPUI.open = null; render(); return; }
  CLIPUI.open = key;
  CLIPUI.sel[key] = null;
  render();
  if (!CLIPUI.segs[key]) {
    try { CLIPUI.segs[key] = (await api(`/api/highlights/${key}/segments`)).segments; }
    catch (e) { CLIPUI.segs[key] = []; toast(e.message); }
    if (CLIPUI.open === key) render();
  }
}

/* Selection is by sentence: they are bounded by the pauses the speaker
   actually took, so a clip cut on them opens and closes cleanly. First tap
   picks one, the next extends the run, tapping the only selected one clears. */
function pickSentence(key, i) {
  const cur = CLIPUI.sel[key];
  if (!cur) CLIPUI.sel[key] = {a: i, b: i};
  else if (cur.a === i && cur.b === i) CLIPUI.sel[key] = null;
  else if (i < cur.a) CLIPUI.sel[key] = {a: i, b: cur.b};
  else CLIPUI.sel[key] = {a: cur.a, b: i};
  render();
}

async function applyTrim(key) {
  const sel = CLIPUI.sel[key], segs = CLIPUI.segs[key] || [];
  if (!sel || !segs.length) return;
  try {
    await api(`/api/highlights/${key}/trim`, {method: 'POST',
      body: {start: segs[sel.a].start, end: segs[sel.b].end}});
    delete CLIPUI.segs[key];
    CLIPUI.sel[key] = null; CLIPUI.open = null; CLIPS = null;
    toast('Trimmed'); render();
  } catch (e) { toast(e.message); }
}

function trimBoxHtml(c) {
  const segs = CLIPUI.segs[c.key];
  if (!segs) return `<div class="trimbox"><div class="empty"><span class="spin"></span></div></div>`;
  if (!segs.length) return `<div class="trimbox"><div class="hint">
    No transcript for this stretch yet, so there is nothing to trim by.</div></div>`;
  const sel = CLIPUI.sel[c.key];
  const whole = !sel || (sel.a === 0 && sel.b === segs.length - 1);
  const picked = sel ? segs.slice(sel.a, sel.b + 1) : [];
  const secs = picked.reduce((n, s) => n + (s.end - s.start), 0);
  return `<div class="trimbox">
    <div class="hint">Tap a sentence to keep it; tap another to extend.</div>
    ${segs.map((s, i) => `<button class="sent ${sel && i >= sel.a && i <= sel.b ? 'on' : ''}"
        onclick="pickSentence('${c.key}', ${i})">
        ${i === 0 || segs[i-1].speaker !== s.speaker
          ? `<span class="who">${esc(s.speaker)}</span>` : ''}${esc(s.text)}</button>`).join('')}
    <div class="acts">
      <button class="btn primary" onclick="applyTrim('${c.key}')"
        ${whole ? 'disabled' : ''}>Trim to ${secs ? Math.round(secs) + 's' : 'selection'}</button>
      <button class="btn" onclick="toggleTrim('${c.key}')">Done</button>
    </div>
    <div class="hint" style="margin-top:9px">Trimming only ever narrows a clip —
      the audio outside what you saved is not part of it.</div>
  </div>`;
}

function renderClips() {
  if (CLIPS === null) {
    loadClips();
    el.innerHTML = '<div class="empty"><span class="spin"></span></div>';
    return;
  }
  el.innerHTML = `
    <div class="bigtitle"><h2>Clips</h2></div>
    ${CLIPS.length ? `<div class="card">${CLIPS.map(c => `
      <div class="clipcard">
        <div class="q ${c.quote ? '' : 'none'} ${CLIPUI.expanded[c.key] ? 'open' : ''}"
             onclick="toggleQuote('${c.key}')">${c.quote
          ? esc(c.quote) : 'Saved — the words appear once this episode is transcribed.'}</div>
        ${(c.quote || '').length > QUOTE_CLAMP ? `<div class="more"
          onclick="toggleQuote('${c.key}')">${CLIPUI.expanded[c.key]
            ? 'Show less' : 'Show all'}</div>` : ''}
        <div class="cite">
          ${c.speaker_name ? `<b>${esc(c.speaker_name)}</b>` : ''}
          <span>${esc(c.feed_title || '')}</span>
          ${c.published_ts ? `<span>· ${dateShort(c.published_ts)}</span>` : ''}
          <span>· ${Math.round(c.seconds || 0)}s</span>
          ${(c.pieces || []).length > 1
            ? `<span>· ${c.pieces.length} pieces</span>` : ''}
        </div>
        ${c.note ? `<div class="note">${esc(c.note)}</div>` : ''}
        <div class="acts">
          <button class="btn" onclick="playClip(this, '${c.key}')">▶ Play</button>
          <button class="btn" onclick="toggleTrim('${c.key}')">
            ${CLIPUI.open === c.key ? 'Close' : 'Trim'}</button>
          <button class="btn" onclick="shareClip('${c.key}')">Share</button>
          <button class="btn" onclick="setHash('#/episode/${c.episode_key}')">Episode</button>
        </div>
        ${CLIPUI.open === c.key ? trimBoxHtml(c) : ''}
      </div>`).join('')}</div>`
    : `<div class="card"><div class="empty">
        Nothing saved yet.<br><br>While an episode is playing, tap ✂ in the
        player to keep the last ${Math.round((STATE.highlight_lookback || 40))}
        seconds you just heard.</div></div>`}`;
}

/* ---- nav -------------------------------------------------------------- */
/* The URL hash is the single source of navigation truth: tabs are #/home,
   drill-downs are #/feed/<slug>, #/episode/<key>, #/job/<id>, #/add. The
   browser's back gesture, reloads and shared links behave like a web page.
   Old bookmark hashes from the tab-bar era map onto the new shell. */
const TABS = ['home', 'library', 'clips', 'jobs'];
const ALIAS = {listen: 'home', search: 'home', feeds: 'library',
               podcasts: 'library', speakers: 'library', people: 'library'};

function syncTabs() {
  // A drill-down highlights the tab it lives under, not the one it was
  // reached from — the highlight answers "where am I", not "how did I get here".
  const t = VIEW ? ({feed: 'library', episode: 'library', add: 'library',
                     job: 'jobs'})[VIEW.kind] : TAB;
  document.querySelectorAll('#tabbar button').forEach(
    b => b.classList.toggle('on', b.dataset.tab === t));
}

function go(tab) {
  // Re-tapping the tab you are on scrolls to the top — platform convention.
  if (!VIEW && TAB === tab) {
    delete SCROLLPOS['#/' + tab];
    window.scrollTo({top: 0, behavior: 'smooth'});
    return;
  }
  setHash('#/' + tab);
}

/* Where you were on each screen, so coming back lands where you left off.
   Saved continuously; restored once per navigation, after the render. */
const SCROLLPOS = {};
let RESTORING = false;
window.addEventListener('scroll', () => {
  if (!RESTORING) SCROLLPOS[location.hash] = window.scrollY;
  document.getElementById('topbar').classList.toggle('on', window.scrollY > 40);
}, {passive: true});

function restoreScroll() {
  const y = SCROLLPOS[location.hash] || 0;
  RESTORING = true;
  requestAnimationFrame(() => {
    window.scrollTo(0, y);
    requestAnimationFrame(() => { RESTORING = false; });
  });
}

function route() {
  HIST++;
  closeSheet();
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  let head = parts[0] || 'home';
  head = ALIAS[head] || head;
  const rest = parts.slice(1).join('/');
  if (head === 'feed' && rest) { TAB = 'library'; loadFeedView(rest); }
  else if (head === 'episode' && rest) { TAB = 'library'; loadEpisodeView(rest); }
  else if (head === 'job' && rest) { TAB = 'jobs'; loadJobView(parseInt(rest, 10)); }
  else if (head === 'add') { TAB = 'library'; VIEW = {kind: 'add'}; render(); }
  else {
    TAB = TABS.includes(head) ? head : 'home';
    VIEW = null;
    if (TAB === 'home') { loadListen(); if (DIGESTS === null) loadDigests();
                          if (PEOPLE === null) loadPeople(); }
    if (TAB === 'library') { if (PEOPLE === null) loadPeople(); }
    render();
    restoreScroll();
  }
  syncTabs();
}
window.addEventListener('hashchange', route);
document.querySelectorAll('#tabbar button').forEach(
  b => b.onclick = () => go(b.dataset.tab));

document.getElementById('np-rate').textContent = RATE + '×';
if (!location.hash) history.replaceState(null, '', '#/home');
route();
refresh();
</script></body></html>
"""
