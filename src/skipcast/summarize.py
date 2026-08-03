"""Episode summaries.

Gemini by default; the Anthropic path is kept behind a config switch so the two
can be compared on the same episode. This is the only part of skipcast that
talks to a cloud service — set [summary] enabled = false to keep the pipeline
local and still get the transcript.

The prompt is genre-aware on purpose. A generic "summarise this" produces the
same shapeless paragraph for a markets show and a history show, and drops
exactly the details that make each worth listening to — the tickers and price
targets in one, the dates and figures in the other. The model is asked to work
out what kind of show it is first, then extract accordingly.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

from .config import Config

# Per 1M tokens (input, output). Used only for the cost estimate in the logs.
PRICING = {
    "gemini-3.6-flash": (1.50, 7.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
}

DEFAULT_MODEL = {"gemini": "gemini-3.6-flash", "anthropic": "claude-opus-5"}

SYSTEM = """You summarise podcast episodes from speaker-attributed transcripts.

The transcript is machine-produced: speaker names come from voice matching and \
the words from speech recognition, so both contain errors. Attribute a claim to \
a speaker only when the transcript supports it. Where a name is missing you \
will see a cluster label like SPEAKER_03 — refer to that person by role or by \
what they argue, never invent a name. Numbers and proper nouns are the most \
common transcription errors: if one looks garbled, say so rather than \
reporting it confidently.

Write for someone deciding whether to listen, and wanting to follow the \
argument if they don't. Cover what was discussed in the order it came up, \
including the substance of disagreements — who took which position and why. \
Give the timestamp that opens each major topic so the reader can jump there.

Do not pad. No preamble, no "in this episode", no restating the title. If the \
episode is thin, say so briefly rather than inflating it.

## Match the extraction to the kind of show

Work out what kind of show this is from the title, the show description and the \
content itself, then hunt for the details that matter for that kind of show. \
Most episodes are a mix — apply whichever fits each topic.

- Markets, finance, investing: every specific ticker, company, price target, \
valuation, position, trade or market call — with who made it, any timeframe, \
and how confident they sounded. Note when someone discloses that they hold a \
position, and separate a firm call from thinking out loud.
- History: dates, durations, place names, people, and the causal chain between \
events. Keep numbers exact (casualties, distances, sums of money, populations). \
Separate established record from the host's interpretation or speculation.
- News, politics, current affairs: the claim, who made it, what evidence was \
offered, and whether a disagreement was about facts or values. Note predictions \
with their timeframe.
- Technology: product and company names, version numbers, benchmarks, funding \
amounts and valuations, technical claims and whether anything backed them up.
- Science, medicine, health: the mechanism or study under discussion, sample \
sizes, effect sizes, and the caveats actually stated. Any actionable advice, \
with how strong the evidence for it was said to be.
- Interviews and profiles: the guest's background and central argument, the \
most revealing exchange, and anything they conceded or that cut against their \
usual position.
- True crime, investigative: timeline, names, and what evidence was actually \
discussed versus inferred. What remains unresolved. Never present speculation \
as established.
- Sports: results, statistics, injuries, transactions, and predictions with \
who made them.
- Comedy, culture, conversation: the through-line and any real recommendations \
or information carried along the way. Do not try to summarise jokes."""

TEMPLATE = """{show_block}Episode title: {title}

{note}

<transcript>
{transcript}
</transcript>

Write the summary in Markdown with exactly these sections:

## In one paragraph
What the episode covered and what, if anything, was actually settled.

## Topics
One `###` heading per substantive topic, opening with its timestamp. Under \
each: what was discussed, who argued what, and where they disagreed.

## {specifics}
The concrete details this kind of show turns on, following the guidance above. \
Rename this heading to fit the show — "Calls and positions" for a markets \
show, "Dates and figures" for history, "Claims and evidence" for news, and so \
on. Attribute each item to whoever said it. If the episode genuinely has no \
such specifics, write one line saying so and move on.

## Worth knowing
Three to eight bullets a listener would want to remember, each attributed.

{index_block}"""

# Asked for in the same call as the prose rather than a second one: the
# transcript is the expensive half of the request, and sending it twice to get
# the same facts in a different shape doubles the cost of every episode.
INDEX_BLOCK = """Then, after the Markdown and separated from it, emit one \
fenced code block tagged `json` and nothing after it. It carries the same \
findings in machine-readable form, for search and cross-episode indexing:

```json
{
  "kind": "one of: markets, history, news, technology, science, interview, \
truecrime, sports, culture, other",
  "kind_label": "how you would describe this show in three or four words",
  "topics": [
    {"title": "short topic name", "at": "12:34",
     "speakers": ["who drove this topic"],
     "one_line": "what it settled, in one sentence"}
  ],
  "specifics": [
    {"type": "ticker | company | price_target | position | prediction | date | \
figure | claim | study | product | person | place | recommendation | other",
     "value": "the thing itself, short — NVDA, 1588, Ozempic, Battle of Lepanto",
     "detail": "what was actually said about it, one sentence",
     "speaker": "who said it, or the cluster label if unnamed",
     "at": "12:34",
     "confidence": "firm | hedged | uncertain"}
  ],
  "interstitials": [
    {"kind": "ad | housekeeping | intro | outro | banter",
     "from": "12:34", "to": "14:02",
     "what": "what this stretch is, in a few words",
     "confidence": "certain | likely | unsure"}
  ]
}
```

Rules for that block:
- `at` is the timestamp from the transcript, copied verbatim — `12:34` or \
`1:02:33`. Never estimate one; omit the field if the transcript does not show it.
- `topics` must match the `###` headings above, in the same order.
- `specifics` covers the same ground as the specifics section — every ticker, \
date, figure, claim or call worth remembering, one entry each. Twenty is a lot; \
an episode with none gets an empty list.
- `confidence` is about how the speaker said it: `firm` for a stated position, \
`hedged` for thinking out loud, `uncertain` where the transcript itself looks \
garbled.
- Attribute to a real transcript speaker, or omit `speaker`. Never invent a name.

`interstitials` are stretches that are not the show: read advertisements and \
sponsor spots, housekeeping (merch, live dates, "like and subscribe", patron \
thanks), the scripted intro before the episode starts and the outro after it \
ends. These get removed from the listener's copy, so the standard is high:

- Mark a stretch only if you can point at where it starts and where it ends. \
`from` and `to` are transcript timestamps copied verbatim; an entry missing \
either is useless and should be left out.
- A host discussing a product, a company or their own work is not an \
advertisement. A read spot has the shape of one: a pitch, a benefit, an offer, \
a code or a URL. When it is argument rather than copy, leave it.
- `banter` means a stretch with no informational content at all, not a \
digression you found less interesting. Most episodes have none. Use it \
sparingly or not at all.
- `confidence` is `certain` for an unmistakable read spot, `likely` where the \
shape is right but the boundary is fuzzy, `unsure` for anything else. Prefer \
`unsure` to guessing.
- Under-marking is the safe error. A missed ad costs a listener ninety \
seconds; a wrongly cut stretch destroys part of the episode they wanted. If in \
doubt, leave it out — an episode with no interstitials gets an empty list."""


class SummaryError(RuntimeError):
    pass


@dataclass
class Summary:
    markdown: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int   # includes thinking, which bills at the output rate
    thinking_tokens: int = 0
    # Parsed from the JSON block. Empty when the model did not produce one or
    # produced something unparseable — the prose is the deliverable, the
    # structure is what makes it queryable.
    data: dict = field(default_factory=dict)

    @property
    def approx_cost_usd(self) -> float:
        rate_in, rate_out = PRICING.get(self.model, (0.0, 0.0))
        return self.input_tokens / 1e6 * rate_in + self.output_tokens / 1e6 * rate_out


def api_key(provider: str) -> str | None:
    names = (
        ("GEMINI_API_KEY", "GOOGLE_API_KEY") if provider == "gemini"
        else ("ANTHROPIC_API_KEY",)
    )
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def available(cfg: Config) -> bool:
    return bool(api_key(cfg.summary.provider))


def missing_key_message(provider: str) -> str:
    name = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
    return (
        f"{name} is not set. Add it to the .env file next to config.toml "
        "(launchd does not read your shell profile, so exporting it in ~/.zshrc "
        "will not reach the background service), or set [summary] enabled = "
        "false to skip summaries."
    )


def _resolve_model(cfg: Config) -> str:
    return cfg.summary.model or DEFAULT_MODEL.get(cfg.summary.provider, "")


def _build_prompt(transcript_text: str, title: str, show: str | None,
                  show_description: str | None, note: str,
                  structured: bool = True) -> str:
    show_block = ""
    if show:
        show_block = f"Show: {show}\n"
        if show_description:
            show_block += f"Show description: {show_description.strip()[:600]}\n"
        show_block += "\n"
    return TEMPLATE.format(
        show_block=show_block,
        title=title,
        note=note or "Summarise the whole episode.",
        transcript=transcript_text,
        specifics="Specifics",
        index_block=INDEX_BLOCK if structured else "",
    )


# ---- structured block ------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)
_STAMP = re.compile(r"(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)")


def timestamp_seconds(value) -> float | None:
    """Parse a transcript timestamp — 12:34, 1:02:33 — into seconds.

    Models hand back all sorts of things here: a range, a stamp in brackets, a
    bare number of minutes. Anything that does not clearly resolve returns None
    rather than a confident wrong number, because these become jump links.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    m = _STAMP.search(str(value))
    if not m:
        return None
    hours, minutes, seconds = m.group(1), int(m.group(2)), int(m.group(3))
    return float(int(hours or 0) * 3600 + minutes * 60 + seconds)


def split_structured(text: str) -> tuple[str, dict]:
    """Separate the prose from the trailing JSON block.

    The prose is what a person reads, so the block is stripped out of it
    whether or not it parsed — a summary ending in forty lines of JSON is worse
    than one with no index at all.
    """
    m = _FENCE.search(text.strip())
    if not m:
        return text.strip(), {}
    prose = text[:m.start()].strip()
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return prose, {}
    if not isinstance(data, dict):
        return prose, {}
    return prose, normalize_index(data)


def normalize_index(data: dict) -> dict:
    """Resolve timestamps to seconds and drop entries that carry nothing.

    Everything here is defensive: this data is model output, and it feeds jump
    links and, later, a cross-episode index.
    """
    out: dict = {
        "kind": str(data.get("kind") or "other")[:40],
        "kind_label": str(data.get("kind_label") or "")[:80],
        "topics": [],
        "specifics": [],
        "interstitials": [],
    }
    for t in data.get("topics") or []:
        if not isinstance(t, dict) or not (t.get("title") or "").strip():
            continue
        out["topics"].append({
            "title": str(t["title"]).strip()[:200],
            "at": str(t.get("at") or "").strip()[:12],
            "at_seconds": timestamp_seconds(t.get("at")),
            "speakers": [str(s)[:80] for s in (t.get("speakers") or [])
                         if isinstance(s, (str, int, float))][:8],
            "one_line": str(t.get("one_line") or "").strip()[:400],
        })
    for s in data.get("specifics") or []:
        if not isinstance(s, dict) or not (s.get("value") or "").strip():
            continue
        confidence = str(s.get("confidence") or "").strip().lower()
        out["specifics"].append({
            "type": str(s.get("type") or "other").strip().lower()[:40],
            "value": str(s["value"]).strip()[:120],
            "detail": str(s.get("detail") or "").strip()[:500],
            "speaker": str(s.get("speaker") or "").strip()[:80],
            "at": str(s.get("at") or "").strip()[:12],
            "at_seconds": timestamp_seconds(s.get("at")),
            "confidence": confidence if confidence in ("firm", "hedged", "uncertain")
                          else "",
        })
    out["interstitials"] = normalize_interstitials(data.get("interstitials"))
    return out


# An interstitial shorter than this is either a mis-parse or not worth a join;
# one longer than this is almost certainly the model swallowing real content.
MIN_INTERSTITIAL = 8.0
MAX_INTERSTITIAL = 600.0


def normalize_interstitials(raw) -> list[dict]:
    """Ad reads and housekeeping, as ranges we would be willing to cut.

    Stricter than the other two lists, because these do not annotate the
    episode — they remove part of it. Anything without both ends, or with ends
    that do not make sense, is dropped rather than repaired: a guessed boundary
    here takes real audio with it.
    """
    kinds = {"ad", "housekeeping", "intro", "outro", "banter"}
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        start = timestamp_seconds(item.get("from"))
        end = timestamp_seconds(item.get("to"))
        if start is None or end is None:
            continue
        span = end - start
        if span < MIN_INTERSTITIAL or span > MAX_INTERSTITIAL:
            continue
        kind = str(item.get("kind") or "").strip().lower()
        confidence = str(item.get("confidence") or "").strip().lower()
        out.append({
            "kind": kind if kind in kinds else "other",
            "from": str(item.get("from") or "").strip()[:12],
            "to": str(item.get("to") or "").strip()[:12],
            "from_seconds": start,
            "to_seconds": end,
            "seconds": round(span, 2),
            "what": str(item.get("what") or "").strip()[:200],
            "confidence": (confidence if confidence in ("certain", "likely", "unsure")
                           else "unsure"),
        })
    out.sort(key=lambda i: i["from_seconds"])
    return out


# ---- providers -------------------------------------------------------------
def _summarize_gemini(prompt: str, cfg: Config) -> Summary:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover
        raise SummaryError("google-genai is not installed. Run: uv sync") from exc

    model = _resolve_model(cfg)
    client = genai.Client(api_key=api_key("gemini"))

    # Gemini 3.x thinks by default, and thinking counts against
    # max_output_tokens — a trivial two-line summary spent 350 of a 400 token
    # budget on thinking and returned 36 tokens of text. Left alone, a real
    # summary gets silently truncated. Summarising a transcript is extraction
    # rather than deep reasoning, so keep thinking low and budget for it.
    thinking = None
    level = (cfg.summary.thinking or "").strip().upper()
    if level and level != "DEFAULT":
        try:
            thinking = types.ThinkingConfig(thinking_level=level)
        except Exception as exc:  # noqa: BLE001
            raise SummaryError(
                f"invalid [summary] thinking value '{cfg.summary.thinking}' — "
                "use minimal, low, medium, high, or default"
            ) from exc

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                max_output_tokens=cfg.summary.max_tokens,
                thinking_config=thinking,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — SDK raises a wide variety
        msg = str(exc)
        if "API_KEY_INVALID" in msg or "API key not valid" in msg:
            raise SummaryError("the Gemini API key was rejected — check .env") from exc
        if "NOT_FOUND" in msg or "not found" in msg.lower():
            raise SummaryError(
                f"model '{model}' was not found. Set [summary] model in "
                "config.toml to one the API offers."
            ) from exc
        raise SummaryError(f"Gemini request failed: {msg}") from exc

    text = (response.text or "").strip()
    if not text:
        # A blocked prompt or an output-token cap both land here; the feedback
        # object says which.
        feedback = getattr(response, "prompt_feedback", None)
        reason = getattr(feedback, "block_reason", None) if feedback else None
        finish = None
        if response.candidates:
            finish = getattr(response.candidates[0], "finish_reason", None)
        raise SummaryError(
            "Gemini returned an empty summary"
            + (f" (blocked: {reason})" if reason else "")
            + (f" (finish_reason: {finish})" if finish and not reason else "")
        )

    # A summary cut off mid-sentence is worse than none — it looks complete.
    finish = str(getattr(response.candidates[0], "finish_reason", "") or "")
    if "MAX_TOKENS" in finish:
        raise SummaryError(
            f"summary hit the {cfg.summary.max_tokens}-token ceiling and was "
            "truncated. Raise [summary] max_tokens, or lower [summary] thinking "
            "— thinking tokens come out of the same budget."
        )

    usage = getattr(response, "usage_metadata", None)
    # Thinking tokens are billed at the output rate, so count them in the cost.
    visible = getattr(usage, "candidates_token_count", 0) or 0
    thoughts = getattr(usage, "thoughts_token_count", 0) or 0
    return Summary(
        markdown=text,
        provider="gemini",
        model=model,
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=visible + thoughts,
        thinking_tokens=thoughts,
    )


def _summarize_anthropic(prompt: str, cfg: Config) -> Summary:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SummaryError("anthropic SDK is not installed. Run: uv sync") from exc

    model = _resolve_model(cfg)
    client = anthropic.Anthropic(api_key=api_key("anthropic"))
    try:
        # Streaming: a long transcript in and a long summary out would risk an
        # HTTP timeout on a plain create().
        with client.beta.messages.stream(
            model=model,
            max_tokens=cfg.summary.max_tokens,
            system=SYSTEM,
            output_config={"effort": cfg.summary.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        raise SummaryError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise SummaryError(f"could not reach the Claude API: {exc}") from exc

    # A refusal returns HTTP 200 with empty or partial content — check the stop
    # reason before reading the blocks.
    if message.stop_reason == "refusal":
        detail = getattr(message, "stop_details", None)
        category = getattr(detail, "category", None) if detail else None
        raise SummaryError(
            "the model declined to summarise this episode"
            + (f" (category: {category})" if category else "")
        )

    text = "".join(
        b.text for b in message.content if getattr(b, "type", "") == "text"
    ).strip()
    if not text:
        raise SummaryError(f"empty summary (stop_reason: {message.stop_reason})")

    return Summary(
        markdown=text,
        provider="anthropic",
        model=message.model,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
    )


def summarize(transcript_text: str, title: str, cfg: Config,
              show: str | None = None, show_description: str | None = None,
              note: str = "", structured: bool = True) -> Summary:
    provider = cfg.summary.provider
    if provider not in ("gemini", "anthropic"):
        raise SummaryError(
            f"unknown [summary] provider '{provider}' — use gemini or anthropic"
        )
    if not api_key(provider):
        raise SummaryError(missing_key_message(provider))

    prompt = _build_prompt(transcript_text, title, show, show_description, note,
                           structured=structured)
    runner = _summarize_gemini if provider == "gemini" else _summarize_anthropic
    result = runner(prompt, cfg)

    if structured:
        result.markdown, result.data = split_structured(result.markdown)

    cost = result.approx_cost_usd
    thinking = (
        f" ({result.thinking_tokens} thinking)" if result.thinking_tokens else ""
    )
    print(
        f"[summary] {result.model}: {result.output_tokens} tokens out{thinking}"
        + (f", ~${cost:.3f}" if cost else ""),
        file=sys.stderr,
    )
    if structured:
        if result.data:
            found = result.data["interstitials"]
            extra = ""
            if found:
                total = sum(i["seconds"] for i in found) / 60
                extra = f", {len(found)} interstitial(s) totalling {total:.1f} min"
            print(
                f"[summary] indexed {len(result.data['topics'])} topics, "
                f"{len(result.data['specifics'])} specifics{extra} "
                f"({result.data.get('kind_label') or result.data['kind']})",
                file=sys.stderr,
            )
        else:
            # Worth saying out loud: the prose is fine, but this episode will
            # have no topic jump links and nothing for the index to read.
            print("[summary] no structured block returned — prose only",
                  file=sys.stderr)
    return result
