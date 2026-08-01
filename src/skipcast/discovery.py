"""Finding podcasts by name.

The only outward call skipcast makes that is not fetching a feed or an episode:
a read-only lookup against Apple's public podcast directory, which is how you
turn "All-In" into an RSS URL without hunting for it. No account, no key, and
nothing sent but the search term. Turn it off with [serve] enable_search.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
USER_AGENT = "skipcast/0.1"


class SearchError(RuntimeError):
    pass


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        raise SearchError(f"podcast search failed: {exc}") from exc


def search(term: str, limit: int = 20) -> list[dict]:
    if not term.strip():
        return []
    qs = urllib.parse.urlencode({
        "term": term.strip(), "entity": "podcast", "limit": max(1, min(limit, 50)),
    })
    data = _fetch(f"{SEARCH_URL}?{qs}")
    out = []
    for r in data.get("results", []):
        feed_url = r.get("feedUrl")
        if not feed_url:
            continue  # nothing we can subscribe to
        out.append({
            "title": r.get("collectionName") or "(untitled)",
            "author": r.get("artistName") or "",
            "feed_url": feed_url,
            "artwork": r.get("artworkUrl100") or r.get("artworkUrl60") or "",
            "episode_count": r.get("trackCount") or 0,
            "genre": r.get("primaryGenreName") or "",
        })
    return out
