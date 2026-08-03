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

### Cutting someone from one show but not another

The `skip` flag is global: marking Jason skip cuts him from every feed. A
per-feed override sits on top of it, which is what you want the first time a
voice you cut from their own show turns up as a guest somewhere you want to
hear them.

```bash
uv run skipcast speakers --feed the-rest-is-history --unskip "Jason Calacanis"
uv run skipcast speakers --feed all-in --skip "Some Guest"
uv run skipcast speakers --feed the-rest-is-history --clear "Jason Calacanis"
```

`--unskip` with `--feed` means *keep them here even though they are cut
everywhere else*, which is not the same as `--clear`: clearing removes the
override and hands the decision back to the global flag. `skipcast speakers`
lists every override under the main table, and the control panel has the same
three-way choice per speaker on each podcast's page.

Overrides only affect future cuts. Episodes already processed need a re-cut.

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

## Phase 4 — search and structured summaries

Transcription was already happening for the summariser's benefit. These two
make what it produced answerable.

### Searching everything ever said

```bash
uv run skipcast index                      # build it from the transcripts on disk
uv run skipcast search "margin call"
uv run skipcast search "Elizabeth" --feed the-rest-is-history
uv run skipcast search "nvidia*" --speaker "Jason Calacanis"
```

SQLite FTS5 over every stored transcript. Quote a phrase to search it as a
phrase, end a word with `*` for a prefix; anything else you type is treated as
words to find rather than query syntax, so an apostrophe or a stray `AND` does
not become an error. The control panel has the same thing under **Search**,
with a jump button on every hit.

Indexing happens automatically at the end of each transcription. `skipcast
index` exists for transcripts that predate it — `--missing` skips what is
already indexed. The index is derived data and can be thrown away and rebuilt
at any time; the panel has a **Rebuild** link for that.

Hits are matched at *passage* granularity rather than per speaker turn. A
diarized turn can run two minutes, and "where was this said" answered with the
moment the speaker started talking is not an answer, so long turns are split at
sentence boundaries and timed by interpolation across the turn.

### Two clocks

Everything downstream of diarization is timed against the original audio: the
segments, the transcript, and every timestamp in a summary. What the phone
plays is the cut. Those clocks diverge the moment anything is removed.

So every search hit carries both — where it was said, and where that lands in
the edited file — and the jump button uses the second. When the moment was
removed outright, skipcast says so and offers to play it from the retained
original instead of silently seeking somewhere else.

That mapping lives in `timeline.py`, is reconstructed from the cut log, and
accounts for crossfades: each join overlaps its two pieces, so positions after
it shift by the crossfade length.

### Structured summaries

The summariser now returns its findings twice: the Markdown you read, and a
JSON block carrying the same material in a form other things can use. It lands
in `<episode>.summary.json` next to the prose:

- `kind` and `kind_label` — the genre it decided the show was
- `topics` — each with the timestamp that opens it, who drove it, one line on
  what it settled
- `specifics` — every ticker, date, figure, claim, study or product worth
  keeping, attributed, timestamped, and marked `firm` / `hedged` / `uncertain`
  depending on how it was said

Both come out of a single request. The transcript is the expensive half of the
call, and sending it twice to get the same facts in a different shape would
double the cost of every episode.

The episode page renders topics and specifics as jump buttons. If the model
returns no usable JSON the prose is still saved and displayed — the block is
stripped either way, so a summary never ends in forty lines of JSON.

Episodes summarised before this existed have prose but no structure. **Summarise
again** on the episode page backfills it. That reuses the stored transcript
rather than re-running Whisper — a re-summarise is one API call and about a
minute, where re-transcribing a 90-minute episode is the better part of an hour
of CPU for a transcript that has not changed.

## Phase 5 — following a person

Voice profiles have been durable and cross-show since Phase 1. This is what
they were for.

### Learning a voice without an episode

Labelling works backwards for tracking someone: you cannot follow a person
until they have already turned up in a show you subscribe to and been through
the pipeline. Enrolment takes a clip instead.

```bash
uv run skipcast enroll "Sam Altman" interview.mp3 --start 120 --end 180
```

A minute of them talking is plenty; ten seconds is the floor. The embedding
comes from the same diarization pipeline the episodes use — profiles are only
comparable when they come out of the same model, so this deliberately does not
reach for a lighter-weight embedder.

Two things it refuses. A clip with more than one substantial voice in it, since
an embedding blended across two people matches neither; and a clip too short to
describe a person rather than a room. It also tells you when the voice already
scores highly against someone known, which is how you catch the same person
being enrolled twice under two spellings.

### Person feeds

```bash
uv run skipcast person add "Chamath Palihapatiya" --min-minutes 2
uv run skipcast person build --slug chamath-palihapatiya
uv run skipcast person                     # list them, with their feed URLs
```

Subscribe to `<base_url>/persons/<slug>.xml` and you get every episode any of
your podcasts has processed where that voice appears, each one reduced to just
them. Items are titled `<show>: <episode>`, so you can tell whose show you are
about to hear them on.

Nothing is downloaded, diarized or transcribed for this. It reads the retained
source audio and segments, re-matches identities, and re-cuts — which is why a
rebuild is cheap and why enrolling someone today produces a feed of episodes
you fetched last month. A poll that brings in new episodes refreshes every
person feed automatically.

### Keeping instead of cutting

Person feeds are built by inverting the cut rules: name who to keep and
everyone else goes. The arithmetic is the same, run against the complement, but
two settings are not, and both live under `[cut]`:

- `keep_only_min_cut_seconds` (8s) governs how long *everyone else* has to talk
  before that stretch goes. `min_skip_seconds` is 15s because it is answering
  "do not cut every mhm"; reused here it would leave most of an interview in,
  since a question rarely runs that long. Short exchanges stay in deliberately
  — an answer with the question removed is a person talking to nobody.
- `keep_only_max_skip_fraction` (0.98) replaces the 50% ceiling. Removing 90%
  of an episode is the job here rather than a symptom of a misidentified voice,
  so this only has to catch "kept nothing at all".

In practice, keeping one panellist from a 97-minute episode yields about 12
minutes, of which ~87% is them and the rest is the questions they are
answering.

### One recording, two subscriptions

If you subscribe to a show and to a mirror of it, every appearance would arrive
twice. Person feeds collapse those: same title, same length to the second, one
item — preferring the longest source, which is the least-edited copy. The guid
is derived from skipcast's own key rather than the source episode's, so an
episode appearing in both its show's feed and a person feed does not collide in
your podcast app.

## Phase 6 — the specifics, across everything

Each summary already extracted its tickers, dates, figures and claims into a
JSON block. Those lived one file per episode, which answers "what was in this
episode" and nothing else. `skipcast index` unpacks them into rows so the whole
library can be asked.

```bash
uv run skipcast entities NVDA
uv run skipcast entities --type figure
uv run skipcast entities                 # everything, newest episode first
```

Matching looks at the value and at the detail, so `Anthropic` finds both the
entry named Anthropic and the settlement figure whose description mentions
them. Normalisation is deliberately shallow — casefolding and whitespace only.
`5.2%` and `$20B` are exactly the values that matter here, and stemming them
into `52` and `20b` would make the index worse.

### Watchlists

```bash
uv run skipcast watch add "NVDA"
uv run skipcast watch                    # terms, with what is new since last time
uv run skipcast watch --seen             # mark it all as read
uv run skipcast watch remove "NVDA"
```

Nothing is sent anywhere. This records what is worth being told about and what
you have already seen; the control panel's **Search → Specifics** tab shows the
difference, with a count of what has arrived since you last looked. A term
added today does not announce five years of back catalogue as news — anything
indexed before the term existed counts as already seen.

If you want this to reach your phone rather than waiting for you to open the
panel, that needs a delivery channel — push, email, a message — and picking one
is a decision skipcast has deliberately not made for you.

## Phase 7 — removing what is not the show

Everything up to here removed *people*. This removes *material*: read
advertisements, housekeeping (merch, live dates, patron thanks), and the
scripted intro and outro.

The ranges come from the same request that writes the summary. The transcript
is the expensive half of that call, and asking a second question about it would
double the cost of every episode — so interstitial detection needs `[summary]
enabled`, and with summaries off nothing is detected.

Because the ranges come from the transcript and the transcript comes from the
audio, this runs as a **second cut** after the episode is already playable. The
cut log and the audio are regenerated, so every timestamp mapping — search
hits, topic links — follows automatically.

```toml
[interstitial]
enabled = true
remove = ["ad", "housekeeping", "intro", "outro"]
min_confidence = "likely"
max_fraction = 0.4
```

`banter` is deliberately not in the default list. It is the judgement the model
is worst at and the one where being wrong removes actual content: a digression
you found boring is still the show.

The prompt is written to under-mark rather than over-mark, and says why — a
missed ad costs ninety seconds, a wrongly cut stretch destroys part of the
episode. A host discussing a product is not an advertisement; a read spot has
the shape of one, with a pitch, an offer and a code.

On a 80-minute history episode it found eight interstitials totalling 7.8
minutes — two sponsor reads and a live-show announcement at the top, a block of
three mid-roll ads, and two more late on. Every one began with recognisable ad
copy. The episode page lists what went, with a **check** button that plays each
stretch from the retained original, because a feature that deletes part of an
episode has to show its work.

The feed description names the two separately, since they are different
promises: `removed 27 min of Jason Calacanis and 8 min of ads and housekeeping`.

### Two lanes

Transcription is CPU-bound and slow; diarization runs on the GPU and is not.
Run serially, a five-episode poll spends most of its time with the GPU idle
waiting for Whisper.

Inside `skipcast serve` these now run on separate workers: the poll finishes an
episode at the cut, hands the transcript to a second lane, and moves straight
on to the next episode's diarization. The database is opened in WAL mode with a
busy timeout so two writers do not collide, and job output is routed per thread
so two concurrent jobs do not scramble each other's logs.

The CLI does the slow half inline, because there is no worker running to hand it
to. `skipcast poll` behaves exactly as it always did.

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
