"""Matching diarized clusters against known speakers.

Cosine similarity against every stored profile, taking the best per speaker.
Best-of rather than average-of is deliberate: see the schema note in db.py.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from . import db
from .config import Config

# Marks a cluster as deliberately unnamed (intro music, ad reads, crosstalk
# fragments). Stored nowhere; it just suppresses the "unlabelled" prompt.
IGNORE = "__ignore__"


@dataclass
class Match:
    cluster_label: str
    name: str | None          # set only when the threshold was cleared
    similarity: float         # best score seen, matched or not
    skip: bool
    # The best-scoring speaker regardless of the threshold. Populated even when
    # nothing matched — "unknown" on its own tells you nothing about whether
    # the threshold is nearly right or wildly off.
    closest_name: str | None = None
    runner_up: str | None = None
    runner_up_similarity: float = 0.0
    source: str | None = None  # which stored episode produced the best hit
    speaker_id: int | None = None
    # True when this feed overrides the speaker's global skip flag, either way.
    rule_scope: str = "global"  # global | feed


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def match_cluster(embedding: list[float], profiles: list[dict], threshold: float) -> Match:
    """Best-scoring speaker for one cluster, plus the runner-up for debugging.

    The runner-up matters: a correct match that beats the next candidate by
    0.01 is luck, and that is exactly when the threshold needs looking at.
    """
    best_per_speaker: dict[str, tuple[float, dict]] = {}
    for prof in profiles:
        if not prof["embedding"]:
            continue
        sim = cosine(embedding, prof["embedding"])
        prev = best_per_speaker.get(prof["name"])
        if prev is None or sim > prev[0]:
            best_per_speaker[prof["name"]] = (sim, prof)

    ranked = sorted(best_per_speaker.items(), key=lambda kv: kv[1][0], reverse=True)
    if not ranked:
        return Match("", None, 0.0, False)

    name, (sim, prof) = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    matched = sim >= threshold
    return Match(
        cluster_label="",
        name=name if matched else None,
        similarity=sim,
        skip=bool(prof["skip"]) if matched else False,
        closest_name=name,
        runner_up=runner[0] if runner else None,
        runner_up_similarity=runner[1][0] if runner else 0.0,
        source=prof["source"] if matched else None,
        speaker_id=prof["speaker_id"] if matched else None,
    )


def match_document(doc: dict, conn: sqlite3.Connection, cfg: Config,
                   feed_id: int | None = None) -> list[Match]:
    """Match every cluster in a segments document against stored profiles.

    Pass feed_id wherever the episode's show is known — it is what lets a voice
    be cut from one podcast and kept on another. Without it the global flag
    stands, which is the right answer for a loose file being analysed on its
    own.
    """
    profiles = db.all_profiles(conn)
    overrides = db.feed_rules(conn, feed_id)
    threshold = cfg.identity.match_threshold
    out = []
    for spk in doc["speakers"]:
        emb = spk.get("embedding")
        if not emb:
            out.append(Match(spk["speaker_label"], None, 0.0, False))
            continue
        m = match_cluster(emb, profiles, threshold)
        m.cluster_label = spk["speaker_label"]
        if m.speaker_id is not None and m.speaker_id in overrides:
            m.skip = overrides[m.speaker_id]
            m.rule_scope = "feed"
        out.append(m)
    return out


def store_profile(conn: sqlite3.Connection, doc: dict, cluster_label: str,
                  name: str, source: str) -> None:
    """Name a diarized cluster and keep its embedding as a voice sample."""
    spk = next(
        (s for s in doc["speakers"] if s["speaker_label"] == cluster_label), None
    )
    if spk is None:
        raise ValueError(f"unknown cluster {cluster_label}")
    if not spk.get("embedding"):
        raise ValueError(
            f"{cluster_label} has too little clean speech to profile"
        )
    speaker_id = db.get_or_create_speaker(conn, name)
    db.add_profile(conn, speaker_id, source, cluster_label,
                   spk["embedding"], spk["total_seconds"])


def annotate(doc: dict, matches: list[Match]) -> dict:
    """Write match results back onto the document, in place."""
    by_label = {m.cluster_label: m for m in matches}
    for spk in doc["speakers"]:
        m = by_label.get(spk["speaker_label"])
        if not m:
            continue
        spk["matched_name"] = m.name
        spk["similarity"] = round(m.similarity, 4)
        spk["skip"] = m.skip
        spk["rule_scope"] = m.rule_scope
        spk["closest_name"] = m.closest_name
        spk["runner_up"] = m.runner_up
        spk["runner_up_similarity"] = round(m.runner_up_similarity, 4)
    return doc
