"""Episode summaries via the Claude API.

The only part of skipcast that talks to a cloud service. It sends the
speaker-attributed transcript and gets back a structured summary; set
[summary] enabled = false to keep the whole pipeline local — you still get the
transcript, just no prose summary.

Streaming is used rather than a plain create() because a 90-minute transcript
plus a detailed summary is a long request on both ends, and non-streaming calls
at this max_tokens risk an SDK HTTP timeout.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from .config import Config

SYSTEM = """You summarise podcast episodes from speaker-attributed transcripts.

The transcript is machine-produced: speaker names come from voice matching and \
the words from speech recognition, so both contain errors. Attribute a claim to \
a speaker only when the transcript supports it. If a name is missing you will \
see a cluster label like SPEAKER_03 — refer to that person by role or by what \
they argue, never invent a name.

Write for someone deciding whether to listen and wanting to follow the \
argument if they don't. Cover what was actually discussed, in the order it came \
up, with the substance of disagreements: who took which position and why. \
Include the timestamps that open each major topic so the reader can jump there.

Do not pad. No preamble, no "in this episode", no restating the title. If the \
episode is thin, say so briefly rather than inflating it."""

TEMPLATE = """Here is the transcript of "{title}"{show}.

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

## Worth knowing
Three to eight bullets — specific claims, numbers, predictions or \
recommendations a listener would want to remember. Attribute each one."""


class SummaryError(RuntimeError):
    pass


@dataclass
class Summary:
    markdown: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str

    @property
    def approx_cost_usd(self) -> float:
        # Claude Opus 5: $5 per Mtok in, $25 per Mtok out.
        return self.input_tokens / 1e6 * 5 + self.output_tokens / 1e6 * 25


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def summarize(transcript_text: str, title: str, cfg: Config,
              show: str | None = None, note: str = "") -> Summary:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SummaryError("anthropic SDK not installed. Run: uv sync") from exc

    if not available():
        raise SummaryError(
            "ANTHROPIC_API_KEY is not set. Export it, or set "
            "[summary] enabled = false in config.toml to skip summaries."
        )

    client = anthropic.Anthropic()
    prompt = TEMPLATE.format(
        title=title,
        show=f" from {show}" if show else "",
        note=note or "Summarise the whole episode.",
        transcript=transcript_text,
    )

    try:
        # Streaming: long transcript in, long summary out. The beta endpoint and
        # server-side fallback mean a safety-classifier refusal on one episode's
        # content is retried on another model rather than losing the summary.
        with client.beta.messages.stream(
            model=cfg.summary.model,
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

    # Check stop_reason before reading content: a refusal returns HTTP 200 with
    # an empty or partial content list.
    if message.stop_reason == "refusal":
        detail = getattr(message, "stop_details", None)
        category = getattr(detail, "category", None) if detail else None
        raise SummaryError(
            "the model declined to summarise this episode"
            + (f" (category: {category})" if category else "")
        )

    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    if not text.strip():
        raise SummaryError(f"empty summary (stop_reason: {message.stop_reason})")

    summary = Summary(
        markdown=text.strip(),
        model=message.model,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        stop_reason=message.stop_reason or "",
    )
    print(
        f"[summary] {summary.output_tokens} tokens out, "
        f"~${summary.approx_cost_usd:.3f}",
        file=sys.stderr,
    )
    return summary
