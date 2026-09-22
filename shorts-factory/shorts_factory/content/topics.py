"""Topic backlog: generate ideas, deduplicate against everything the channel has done, pick per format."""
from __future__ import annotations

import json
import logging
from typing import Any

from rapidfuzz import fuzz

from ..config import ChannelCfg
from ..db import DB
from . import prompts
from .llm import LLM
from .schemas import TopicBatch
from .strategy import learnings_text

log = logging.getLogger(__name__)

TITLE_SIMILARITY = 88  # token_set_ratio at/above this = same story told with the same words
KEYWORD_OVERLAP = 0.5  # Jaccard overlap of normalized keywords = same story told differently


def _kw(keywords: list[str]) -> set[str]:
    return {k.strip().lower() for k in keywords if k.strip()}


def is_duplicate(title: str, keywords: list[str], existing: list[tuple[str, list[str]]]) -> bool:
    """Lexical title match OR keyword overlap. Keywords catch paraphrases like
    "The Dancing Plague of 1518" vs "Why people danced to death in Strasbourg"."""
    t, kw = title.lower(), _kw(keywords)
    for et, ek in existing:
        if fuzz.token_set_ratio(t, et.lower()) >= TITLE_SIMILARITY:
            return True
        ek_ = _kw(ek)
        if kw and ek_ and len(kw & ek_) / len(kw | ek_) >= KEYWORD_OVERLAP:
            return True
    return False


def dedupe(ideas: list[dict[str, Any]], existing: list[tuple[str, list[str]]],
           valid_formats: set[str]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    seen = list(existing)
    for idea in ideas:
        if idea["format"] not in valid_formats:
            continue
        if is_duplicate(idea["title"], idea.get("keywords", []), seen):
            continue
        kept.append(idea)
        seen.append((idea["title"], idea.get("keywords", [])))
    return kept


def generate_topics(llm: LLM, db: DB, ch: ChannelCfg, n: int = 60) -> int:
    existing = [(r["title"], json.loads(r["keywords"])) for r in db.topics(ch.id)]
    batch = llm.structured(
        prompts.topics_system(ch),
        prompts.topics_user(ch, n, [t for t, _ in existing], learnings_text(db.performance(ch.id))),
        TopicBatch,
        context={"channel": ch.id, "n": n, "formats": [f.id for f in ch.formats]},
    )
    ideas = [i.model_dump() for i in batch.ideas]
    kept = dedupe(ideas, existing, {f.id for f in ch.formats})
    db.add_topics(ch.id, kept)
    log.info("%s: %d ideas generated, %d new after dedupe", ch.id, len(ideas), len(kept))
    return len(kept)


def pick_topic(db: DB, ch: ChannelCfg, fmt: str, taken: set[int]) -> dict[str, Any] | None:
    """Highest-priority unused topic of the wanted format, falling back to any format."""
    rows = [r for r in db.topics(ch.id, status="new") if r["id"] not in taken]
    for r in rows:
        if r["format"] == fmt:
            return dict(r)
    return dict(rows[0]) if rows else None
