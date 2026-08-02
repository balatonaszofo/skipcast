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

import os
import sys
from dataclasses import dataclass

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
Three to eight bullets a listener would want to remember, each attributed."""


class SummaryError(RuntimeError):
    pass


@dataclass
class Summary:
    markdown: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int

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
                  show_description: str | None, note: str) -> str:
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
    )


# ---- providers -------------------------------------------------------------
def _summarize_gemini(prompt: str, cfg: Config) -> Summary:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover
        raise SummaryError("google-genai is not installed. Run: uv sync") from exc

    model = _resolve_model(cfg)
    client = genai.Client(api_key=api_key("gemini"))
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                max_output_tokens=cfg.summary.max_tokens,
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

    usage = getattr(response, "usage_metadata", None)
    return Summary(
        markdown=text,
        provider="gemini",
        model=model,
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
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
              note: str = "") -> Summary:
    provider = cfg.summary.provider
    if provider not in ("gemini", "anthropic"):
        raise SummaryError(
            f"unknown [summary] provider '{provider}' — use gemini or anthropic"
        )
    if not api_key(provider):
        raise SummaryError(missing_key_message(provider))

    prompt = _build_prompt(transcript_text, title, show, show_description, note)
    runner = _summarize_gemini if provider == "gemini" else _summarize_anthropic
    result = runner(prompt, cfg)

    cost = result.approx_cost_usd
    print(
        f"[summary] {result.model}: {result.output_tokens} tokens out"
        + (f", ~${cost:.3f}" if cost else ""),
        file=sys.stderr,
    )
    return result
