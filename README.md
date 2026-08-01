# skipcast

Removes specific speakers from podcast episodes and republishes them as a
private RSS feed. Everything runs on one desktop machine; the phone just
subscribes to the generated feed over Tailscale.

**Status: Phase 0.** Diarize an episode and listen to the result. Nothing is
cut yet.

## Why the server keeps its own copy

The pipeline downloads each episode once and serves that exact file. The phone
never fetches from the original CDN. Megaphone, Art19 and Acast do dynamic ad
insertion, so two downloads of the same episode GUID differ in length and in
every offset after the first ad break. Serving our own canonical copy is the
only thing that makes the timestamps mean anything.

## Setup

```bash
brew install uv ffmpeg
uv sync
```

The diarization model is gated. Accept the terms on both repos with the same
HuggingFace account:

- https://hf.co/pyannote/speaker-diarization-3.1
- https://hf.co/pyannote/segmentation-3.0

Then create a read token at https://hf.co/settings/tokens and either export it:

```bash
export HF_TOKEN=hf_xxxxxxxxxxxx
```

(put that in `~/.zshrc` to persist it), or log in once and let
`huggingface_hub` cache it:

```bash
uv run hf auth login
```

Both work — skipcast checks the environment first, then the cached login.

## Phase 0 — analyze

```bash
uv run skipcast analyze /path/to/episode.mp3
```

`analyze` also takes a URL — anything yt-dlp handles, YouTube included:

```bash
uv run skipcast analyze "https://www.youtube.com/watch?v=..."
```

That downloads to `data/sources/<slug>-<id>.mp3` first, then diarizes it. It is
idempotent on the video id, so re-running reuses the local copy; `--refetch`
forces a fresh download. `skipcast fetch <url>` does the download on its own if
you just want the audio.

The file exactly as delivered is kept next to the transcoded MP3
(`.source.webm` and friends) so a bad transcode can be redone offline. Title,
uploader, and source URL land in a `.meta.json` and show up in the preview
header. The duration recorded is what ffprobe says about our copy, never what
the site claimed.

Writes two files next to the audio:

- `episode.segments.json` — `{start, end, speaker_label}` plus per-speaker totals
- `episode.preview.html` — a player with a segment timeline

The preview page is self-contained (no CDN) but references the audio by
relative path, so keep the `.mp3` next to the `.html`. If the browser refuses
to load it from `file://`, the page offers a file picker.

In the preview you can:

- click the timeline or any segment row to jump
- hit **hear** next to a speaker to jump to their longest turn — that is how
  you work out which cluster is which person
- tick **skip** on one or more speakers and turn on **Skip preview**, and
  playback jumps over the regions that would be cut

That last one is the point of Phase 0. It applies the same segment-selection
rules Phase 2 will (merge gap, minimum skip length, inward boundary padding)
and reports how much of the episode would disappear. It seeks rather than
crossfades, so the real output will sound slightly smoother than the preview
does, never rougher.

First run downloads ~1 GB of model weights. Diarization is slower than
realtime — budget several minutes per hour of audio.

## Phase 1 — speaker identity

Diarization only produces anonymous clusters (`SPEAKER_00`, `SPEAKER_06`), and
the numbering is meaningless across episodes. Phase 1 puts names to them once
and recognises the same voices afterwards.

```bash
uv run skipcast label episode.segments.json
```

Opens a local page (127.0.0.1, random-ish port) listing each cluster with its
three longest turns as play buttons — the quickest way to tell who is who.
Type a name and press Enter to store a voice profile; hit **ignore** for intro
music, ad reads and crosstalk fragments. Tick **skip** on anyone you want cut
from future episodes. Hit **Done**, or Ctrl-C the terminal.

```bash
uv run skipcast speakers                    # who is known, and who is skipped
uv run skipcast speakers --skip "Jason"     # cut this voice from now on
uv run skipcast speakers --unskip "Jason"
uv run skipcast speakers --forget "Jason"   # drop them and all their profiles
```

From then on, `analyze` matches each new cluster against stored profiles by
cosine similarity and reports what it found:

```
SPEAKER_02    29.9 min   31.1%   239 seg   = Jason  [SKIP]  (sim 0.83)
SPEAKER_05     4.1 min    4.3%    31 seg   unknown  (closest Chamath 0.44)
```

Anything below the threshold surfaces as unknown for you to label. The
similarity of the *runner-up* is printed too — a correct match that beats the
next candidate by 0.01 is luck, not identification, and that is exactly when
`match_threshold` needs looking at.

### How profiles are stored

A speaker gets **one profile row per source episode**, not a single running
average, and matching takes the best of them. Averaging across shows pulls the
vector toward the middle of every microphone and room it has ever seen, and it
ends up matching nothing well. Keeping samples separate is what lets a profile
survive the jump to a differently-recorded podcast.

Expect cross-show similarity to run lower than cross-episode similarity within
one show — different mic, different room, different codec. It fails safe: an
unrecognised voice surfaces as unknown rather than being cut by mistake.

The `skip` flag is per speaker and global — marking Jason skip cuts him from
every feed. If you ever need it scoped per feed, that is a schema change.

## Phase 2 — cutting

```bash
uv run skipcast cut episode.mp3 episode.segments.json
```

Writes `episode.cut.mp3` and `episode.cuts.json`. Add `-n` to plan without
encoding, or `--speaker "Jason Calacanis"` / `--speaker SPEAKER_06` to override
the stored skip flags for one run.

Selection rules, applied in this order — the order is the point, since
diarization emits fragments rather than turns:

1. merge adjacent same-speaker segments separated by less than `merge_gap_seconds`
2. cut a merged region only if it reaches `min_skip_seconds`; short
   interjections stay in
3. pull each boundary inward by `boundary_padding_seconds` so the cut does not
   clip the kept speaker's first or last syllable
4. union what remains, since speakers overlap during crosstalk
5. refuse the episode outright if more than `max_skip_fraction` would go

Two boundary cases the plain rules get wrong, both handled:

- **At the episode's own start and end** there is no adjacent kept speaker, so
  padding there would only strand a fraction of a second of the removed voice
  against the file boundary. Cuts touching either edge are not padded.
- **Between two back-to-back cuts** the only thing separating them is their own
  padding — again with no kept speaker to protect. That gap is absorbed.
  Set `merge_adjacent_cuts = false` to keep the padding literally.

### Why re-encode instead of stream-copy

Cutting MP3 without re-encoding has to land on frame boundaries, which do not
line up with speech boundaries, and it drags along encoder delay and gapless
padding that shift every timestamp after the first join. Re-encoding costs one
generation of loss that is inaudible on speech. Copy-codec cutting is a later
optimisation and would need frame-accurate boundary snapping to be worth it.

Cutting a 96-minute episode takes about 35 seconds.

### The cut log

`episode.cuts.json` records the config used, every merged region with its
speaker, match name, similarity, timings and whether it was cut or kept (and
why), plus the resulting cut and keep timelines. It is written even on `-n`,
so you can inspect a plan before committing to it. The original download and
the segments JSON are always retained, so a bad cut can be regenerated with
different parameters without re-downloading or re-diarizing.

## Phase 3 — feeds and serving

```bash
uv run skipcast subscribe <feed-url> --slug all-in
uv run skipcast poll --feed all-in --limit 1
uv run skipcast serve
```

Then subscribe your phone to `<base_url>/feeds/<slug>.xml` in AntennaPod.

```bash
uv run skipcast feeds                  # what is subscribed, and its served URL
uv run skipcast episodes               # what has been processed
uv run skipcast episodes --problems    # only failures and refusals
```

### Set base_url before subscribing on your phone

Enclosure URLs in the generated feed are built from `[serve] base_url`. It
defaults to `localhost`, which works for testing on this machine but resolves
to the *phone* if AntennaPod tries it. Set it to this machine's tailnet name:

```toml
[serve]
base_url = "http://your-machine.tailnet-name.ts.net:8730"
```

`skipcast serve` warns if you leave it pointing at localhost.

### Polling

`poll` reads each feed, then for every new episode: downloads it, diarizes it,
matches clusters against known voices, cuts the flagged ones, and marks it
ready. It is **idempotent on episode GUID** — an episode already processed is
skipped without re-downloading, so poll can run on a timer.

Each stage records its output path as it completes, so an interrupted run
resumes rather than restarting. A failed episode is retried next time; one
refused by `max_skip_fraction` is not, since the rules already decided.

`[poll] max_episodes` caps how many of the newest episodes are considered per
run, defaulting to 5. Feeds routinely carry hundreds of back episodes and
diarization runs slower than realtime, so an uncapped first poll would run for
days.

If a substantial cluster is not recognised, poll says so and leaves that
speaker in — it never guesses. Run `skipcast label` on that episode's segments
file to teach it the voice, then `poll --force` to reprocess.

### The generated feed

Original title, description, GUID and publication date are preserved. What
changes:

- **enclosure URLs point at this server**, never the original CDN — the whole
  reason the timestamps are valid
- **`<itunes:duration>` is corrected** to the edited length
- **a line is appended to each description** naming what was removed:
  `skipcast: removed 26 min of Jason Calacanis (27% of the original 97 min).`
- `<itunes:block>Yes</itunes:block>`, so a private feed never gets indexed

Audio is served with full HTTP Range support. Podcast clients seek, resume
part-played episodes and fetch in chunks; without Range an 80-minute episode
cannot be scrubbed.

## Running it on another machine

The repo carries code and config only. Everything in `data/` — downloads, cut
audio, the database — stays local.

```bash
git clone <your-repo-url> skipcast && cd skipcast
brew install uv ffmpeg
uv sync
uv run hf auth login          # after accepting both gated pyannote repos
```

Two things do **not** come across in the repo, and one of them matters:

- **`[serve] base_url` is machine-specific.** Set it to that machine's tailnet
  name. Since `config.toml` is tracked, your local edit will show as a
  modification on every machine; `git update-index --skip-worktree config.toml`
  silences that if it annoys you.
- **Speaker profiles live in `data/skipcast.db`.** A fresh clone knows no
  voices, so it will not cut anyone until you label an episode again. Copy the
  database across if you want to keep them:

```bash
scp other-machine:~/skipcast/data/skipcast.db data/skipcast.db
```

Do not put the project in `~/Documents`, `~/Desktop` or `~/Downloads` if you
intend to use the launchd service. macOS TCC denies launchd agents access to
those folders and the server will fail to start with `Operation not permitted`.

## Automatic operation (macOS)

```bash
uv run skipcast service install     # start at login, restart on crash
uv run skipcast service status
uv run skipcast service uninstall
```

This installs a launchd agent running `skipcast serve`. Polling is scheduled
inside that process rather than as a second launchd job — two processes
diarizing at once would contend for the GPU and the database — so scheduled
polls appear in the control panel's Activity tab alongside manual ones. The
interval is `[poll] interval_hours`; set it to 0 for manual polling only.

The machine has to be awake. A sleeping laptop polls nothing and serves
nothing, which is a good reason to run this on a desktop.

## Configuration

Everything tunable is in [`config.toml`](config.toml): the similarity
threshold, `min_skip_seconds`, crossfade length, boundary padding, and the
`max_skip_fraction` safety ceiling. Nothing that changes output shape is
hardcoded in the Python.

If diarization crashes or produces nonsense on the Apple GPU, set
`device = "cpu"` under `[diarize]`.

Originals and segment JSONs are always retained so a bad cut can be
regenerated with different parameters.
