"""Reading podcast feeds and writing our own.

Reading uses feedparser, which copes with the decades of malformed RSS in the
wild. Writing is done by hand: the generated feed has to carry through
namespaced iTunes elements exactly as podcast clients expect, and a generic
serializer fights that more than it helps.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

USER_AGENT = "skipcast/0.1 (+https://github.com/local/skipcast)"


class FeedError(RuntimeError):
    pass


@dataclass
class Entry:
    guid: str
    title: str
    description: str
    link: str
    published: str          # RFC 822 as given, or rebuilt
    published_ts: int
    enclosure_url: str
    enclosure_type: str
    duration: str | None


def slugify(text: str, limit: int = 40) -> str:
    """Trim on a word boundary — 'friedber' is not a useful feed name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if len(slug) <= limit:
        return slug or "feed"
    cut = slug[:limit]
    if "-" in cut:
        cut = cut[: cut.rindex("-")]
    return cut.strip("-") or "feed"


def episode_key(feed_url: str, guid: str) -> str:
    """Stable public id, independent of database row ids."""
    return hashlib.sha1(f"{feed_url}\n{guid}".encode()).hexdigest()[:12]


def _text(entry, *names) -> str:
    for n in names:
        v = entry.get(n)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, list) and v and isinstance(v[0], dict):
            val = v[0].get("value")
            if val:
                return val
        if isinstance(v, dict) and v.get("value"):
            return v["value"]
    return ""


def parse(url_or_path: str) -> tuple[dict, list[Entry]]:
    import feedparser

    parsed = feedparser.parse(
        url_or_path, agent=USER_AGENT, request_headers={"User-Agent": USER_AGENT}
    )
    if parsed.bozo and not parsed.entries:
        raise FeedError(f"could not parse {url_or_path}: {parsed.get('bozo_exception')}")
    if not parsed.feed:
        raise FeedError(f"no channel data in {url_or_path}")

    f = parsed.feed
    image = ""
    if isinstance(f.get("image"), dict):
        image = f["image"].get("href") or f["image"].get("url") or ""
    meta = {
        "title": _text(f, "title"),
        "description": _text(f, "subtitle", "description", "summary"),
        "link": _text(f, "link"),
        "language": f.get("language") or "en",
        "author": _text(f, "author", "publisher") or f.get("itunes_author", ""),
        "image_url": image,
    }

    entries = []
    for e in parsed.entries:
        enc_url = enc_type = ""
        for link in e.get("links", []):
            if link.get("rel") == "enclosure" and link.get("href"):
                enc_url = link["href"]
                enc_type = link.get("type") or "audio/mpeg"
                break
        if not enc_url:
            continue  # no audio, not an episode we can process

        # A GUID is what makes polling idempotent. Fall back to the enclosure
        # URL only if the feed omits one, which is rare and non-compliant.
        guid = e.get("id") or e.get("guid") or enc_url

        ts = 0
        published = _text(e, "published", "updated")
        struct = e.get("published_parsed") or e.get("updated_parsed")
        if struct:
            ts = int(dt.datetime(*struct[:6], tzinfo=dt.timezone.utc).timestamp())
            if not published:
                published = email.utils.formatdate(ts, usegmt=True)

        entries.append(Entry(
            guid=guid,
            title=_text(e, "title") or "(untitled)",
            description=_text(e, "content", "summary", "description", "subtitle"),
            link=_text(e, "link"),
            published=published,
            published_ts=ts,
            enclosure_url=enc_url,
            enclosure_type=enc_type,
            duration=e.get("itunes_duration"),
        ))

    entries.sort(key=lambda x: x.published_ts, reverse=True)
    return meta, entries


def download_enclosure(url: str, dest: Path, progress: bool = True) -> Path:
    """Fetch the episode once, to our own disk.

    This is the load-bearing decision of the whole project. Megaphone, Art19
    and Acast insert ads dynamically, so two fetches of the same GUID differ in
    length and in every offset after the first break. Every timestamp we
    compute refers to this file and no other, which is why the phone is served
    our copy rather than being pointed back at the CDN.
    """
    import sys

    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(262144):
            fh.write(chunk)
            done += len(chunk)
            if progress and total:
                print(f"\r[poll]   {done / total * 100:5.1f}%  "
                      f"{done / 1e6:.1f}/{total / 1e6:.1f} MB", end="", file=sys.stderr)
    if progress:
        print(file=sys.stderr)
    tmp.replace(dest)
    return dest


# ---- writing --------------------------------------------------------------
def _cdata(text: str) -> str:
    """Descriptions are full of HTML; CDATA keeps it intact."""
    return f"<![CDATA[{(text or '').replace(']]>', ']]]]><![CDATA[>')}]]>"


def _hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def cut_note(row) -> str:
    """The line appended to each episode description."""
    cut = row["cut_seconds"] or 0
    if not cut:
        return "skipcast: nothing removed from this episode."
    keys = row.keys()
    ads = (row["interstitial_seconds"] or 0) if "interstitial_seconds" in keys else 0
    orig = row["original_seconds"] or 0
    pct = (cut / orig * 100) if orig else 0

    # Two different promises, so they are named separately: one is the voice
    # you asked to have removed, the other is material that was never the show.
    parts = []
    speech = cut - ads
    names = row["cut_speakers"] or ""
    if speech > 30 and names:
        parts.append(f"{speech / 60:.0f} min of {names}")
    elif speech > 30:
        parts.append(f"{speech / 60:.0f} min of flagged speakers")
    if ads > 30:
        parts.append(f"{ads / 60:.0f} min of ads and housekeeping")
    if not parts:
        parts.append(f"{cut / 60:.0f} min")
    return (f"skipcast: removed {' and '.join(parts)} "
            f"({pct:.0f}% of the original {orig / 60:.0f} min).")


def render_person_feed(person_row, episodes, base_url: str) -> str:
    """One person's appearances across every show, as a podcast feed.

    Each item is a derived edit, so its title says which show it came from —
    without that the feed is a list of episode names with no way to tell whose
    show you are about to hear them on.
    """
    base = base_url.rstrip("/")
    now = email.utils.formatdate(usegmt=True)
    who = person_row["title"] or person_row["speaker"]
    self_url = f"{base}/persons/{person_row['slug']}.xml"
    blurb = (
        f"Every appearance by {who} that skipcast has processed, across all "
        "subscribed podcasts, with everyone else removed. Generated locally; "
        "the timings are against skipcast's own copy of each episode."
    )

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"'
        ' xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"'
        ' xmlns:content="http://purl.org/rss/1.0/modules/content/"'
        ' xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{escape(who)} (skipcast)</title>",
        f"<link>{escape(base)}</link>",
        f"<description>{_cdata(blurb)}</description>",
        "<language>en</language>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        f"<atom:link href={quoteattr(self_url)}"
        ' rel="self" type="application/rss+xml"/>',
        f"<itunes:author>{escape(who)}</itunes:author>",
        "<itunes:block>Yes</itunes:block>",
    ]

    for e in episodes:
        size = 0
        if e["audio_path"] and Path(e["audio_path"]).is_file():
            size = Path(e["audio_path"]).stat().st_size
        audio_url = f"{base}/persons/audio/{e['key']}.mp3"
        show = e["feed_title"] or e["feed_slug"]
        talk = (e["talk_seconds"] or 0) / 60
        orig = (e["original_seconds"] or 0) / 60
        note = (
            f"skipcast: {who} on {show}. {talk:.0f} min of them, edited out of "
            f"the original {orig:.0f} min."
        )
        description = f"{note}\n\n{(e['description'] or '').rstrip()}"

        out.append("<item>")
        out.append(f"<title>{escape(show)}: {escape(e['title'] or '(untitled)')}</title>")
        if e["link"]:
            out.append(f"<link>{escape(e['link'])}</link>")
        out.append(f"<description>{_cdata(description)}</description>")
        # Derived from our own key, not the source episode's guid: the same
        # episode can appear in this feed and in the show's own feed, and two
        # items sharing a guid is how a podcast client loses one of them.
        out.append(f'<guid isPermaLink="false">{escape(e["key"])}</guid>')
        if e["published"]:
            out.append(f"<pubDate>{escape(e['published'])}</pubDate>")
        out.append(
            f"<enclosure url={quoteattr(audio_url)} length=\"{size}\" "
            'type="audio/mpeg"/>'
        )
        if e["seconds"]:
            out.append(f"<itunes:duration>{_hms(e['seconds'])}</itunes:duration>")
        out.append("</item>")

    out.append("</channel></rss>")
    return "\n".join(out)


def render_feed(feed_row, episodes, base_url: str) -> str:
    base = base_url.rstrip("/")
    now = email.utils.formatdate(usegmt=True)
    title = feed_row["title"] or feed_row["slug"]
    self_url = f"{base}/feeds/{feed_row['slug']}.xml"

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"'
        ' xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"'
        ' xmlns:content="http://purl.org/rss/1.0/modules/content/"'
        ' xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{escape(title)} (skipcast)</title>",
        f"<link>{escape(feed_row['link'] or base)}</link>",
        f"<description>{_cdata(feed_row['description'] or '')}</description>",
        f"<language>{escape(feed_row['language'] or 'en')}</language>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        f"<atom:link href={quoteattr(self_url)}"
        ' rel="self" type="application/rss+xml"/>',
    ]
    if feed_row["author"]:
        out.append(f"<itunes:author>{escape(feed_row['author'])}</itunes:author>")
    if feed_row["image_url"]:
        out.append(f"<itunes:image href={quoteattr(feed_row['image_url'])}/>")
    out.append("<itunes:block>Yes</itunes:block>")  # never index a private feed

    for e in episodes:
        size = 0
        if e["cut_path"] and Path(e["cut_path"]).is_file():
            size = Path(e["cut_path"]).stat().st_size
        audio_url = f"{base}/audio/{e['key']}.mp3"
        description = (e["description"] or "").rstrip()
        description += f"\n\n{cut_note(e)}"
        # The summary goes into the feed too, so it shows up in whatever
        # podcast app you use, not only the control panel.
        summary_path = e["summary_path"] if "summary_path" in e.keys() else None
        if summary_path and Path(summary_path).is_file():
            description += "\n\n" + Path(summary_path).read_text().strip()

        out.append("<item>")
        out.append(f"<title>{escape(e['title'] or '(untitled)')}</title>")
        if e["link"]:
            out.append(f"<link>{escape(e['link'])}</link>")
        out.append(f"<description>{_cdata(description)}</description>")
        guid_text = escape(e["guid"])
        out.append(f'<guid isPermaLink="false">{guid_text}</guid>')
        if e["published"]:
            out.append(f"<pubDate>{escape(e['published'])}</pubDate>")
        out.append(
            f"<enclosure url={quoteattr(audio_url)} length=\"{size}\" "
            'type="audio/mpeg"/>'
        )
        # The corrected duration — the edited length, not the original.
        if e["result_seconds"]:
            out.append(f"<itunes:duration>{_hms(e['result_seconds'])}</itunes:duration>")
        out.append("</item>")

    out.append("</channel></rss>")
    return "\n".join(out)
