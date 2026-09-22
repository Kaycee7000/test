"""Daily trend scouting per channel, and the prompt-ready trend brief built from it."""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

from ..config import ChannelCfg, Settings
from . import collectors as C
from .kb import KnowledgeBase

log = logging.getLogger(__name__)


def scout_channel(kb: KnowledgeBase, s: Settings, ch: ChannelCfg, day: date, fetch: C.Fetch | None = None,
                  force: bool = False) -> dict[str, int]:
    """Collect today's public signals for one channel into the knowledge base (once per day)."""
    key_day = day.isoformat()
    if not force and kb.scouted(ch.id, key_day):
        return {}
    fetch = fetch or C.make_fetch(s.research.user_agent)
    rc, cr = s.research, ch.research
    lang = cr.wikipedia_lang or ch.language
    counts: dict[str, int] = {}

    def add(docs: list[dict[str, Any]], niche: str, ttl: int | None) -> None:
        for d in docs:
            kb.add(d["kind"], niche, ch.language if niche != "*" else lang, d["title"], d["body"], url=d.get("url", ""),
                   source=d.get("source", ""), platform=ch.platform, meta=d.get("meta"), ttl_days=ttl, key=d.get("key"))
        if docs:
            counts[docs[0]["kind"]] = counts.get(docs[0]["kind"], 0) + len(docs)

    def attempt(name: str, fn) -> None:
        try:
            fn()
        except Exception as e:  # one broken source must not stop the others
            log.warning("%s: %s scout failed: %s", ch.id, name, e)

    if "youtube" in rc.scout_sources and ch.platform == "youtube_shorts":
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if api_key:
            queries = (cr.youtube_queries or ch.pillars)[: rc.youtube_max_queries]
            attempt("youtube", lambda: add(C.youtube_trends(
                fetch, api_key, queries=queries, language=ch.language, region=cr.region,
                lookback_days=rc.youtube_lookback_days, per_query=rc.youtube_results_per_query),
                ch.folder, rc.youtube_retention_days))
        else:
            log.warning("YOUTUBE_API_KEY not set: skipping public YouTube trend research")
    if "wikipedia_trending" in rc.scout_sources and not kb.scouted(f"wiki:{lang}", key_day):
        attempt("wikipedia trending", lambda: add(C.wikipedia_trending(fetch, lang, day), "*", 14))
        kb.mark_scouted(f"wiki:{lang}", key_day)
    if "on_this_day" in rc.scout_sources and cr.on_this_day:
        attempt("on this day", lambda: add(C.on_this_day(fetch, lang, day), ch.folder, 3))
    if "rss" in rc.scout_sources:
        for url in cr.rss:
            attempt(f"rss {url}", lambda url=url: add(C.rss_items(fetch, url), ch.folder, 14))
    if "mock" in rc.scout_sources:
        add(C.mock_trends(ch.folder, ch.language), ch.folder, 30)
    kb.mark_scouted(ch.id, key_day)
    log.info("%s: scouted %s", ch.id, counts or "nothing new")
    return counts


def trend_brief(kb: KnowledgeBase, s: Settings, ch: ChannelCfg) -> str:
    """Compact demand signals for the topic strategist."""
    lang = ch.research.wikipedia_lang or ch.language
    parts: list[str] = []
    vids = kb.recent(["trend_video"], niche=ch.folder, language=ch.language,
                     max_age_days=s.research.youtube_lookback_days, limit=15)
    if vids:
        parts.append("TOP RECENT SHORTS IN THIS NICHE (public YouTube data, sorted by velocity):")
        for v in vids:
            m = v["meta"]
            parts.append(f'- "{v["title"]}" | {m.get("views", 0):,} views, {m.get("views_per_hour", 0):,.0f}/hour, '
                         f'{m.get("duration_s", "?")}s | {m.get("channel_title", "")}')
    wiki = kb.recent(["trend_wiki"], language=lang, max_age_days=3, limit=40)
    if wiki:
        parts.append("MOST-READ WIKIPEDIA ARTICLES YESTERDAY (general curiosity; use only what fits the niche):")
        parts.append("; ".join(w["title"] for w in wiki))
    otd = kb.recent(["on_this_day"], niche=ch.folder, language=ch.language, max_age_days=3, limit=12)
    if otd:
        parts.append("ANNIVERSARIES ON THE PUBLISH DATE (a strong timely hook if the story is great):")
        parts += [f"- {o['title']}" for o in otd]
    rss = kb.recent(["rss"], niche=ch.folder, language=ch.language, max_age_days=10, limit=10)
    if rss:
        parts.append("NICHE NEWS HEADLINES:")
        parts += [f"- {r['title']}" for r in rss]
    return "\n".join(parts)
